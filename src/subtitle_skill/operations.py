"""Operation execution: generate / render.

This module is the only place that turns a parsed request into an output
file. It performs, in order: security screening, typed parsing, timeline
validation, deterministic identity computation, reuse check, format
generation, (for render) delegation to the execution skill, output
validation, and provenance recording. No step here makes an editorial
decision about subtitle content -- everything it does is a mechanical
consequence of the typed request.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from . import CONTRACT_VERSION, SKILL_ID, SKILL_VERSION
from . import engine as engine_module
from .engine import burn_in, ffmpeg_skill_script_hash, resolve_ffmpeg_skill_root
from .errors import SubtitleSkillError
from .formats import GENERATORS, SUPPORTED_FORMATS, generate_srt
from .models import SubtitleDocument, SubtitleStyle
from .pathpolicy import PathPolicy
from .provenance import canonical_json, compute_identity, sha256_file
from .security import reject_forbidden_keys
from .validation import SubtitleConstraints, validate_document

SUPPORTED_OPERATIONS = ("generate", "render")

_SIDECAR_SUFFIX = ".subtitle-skill.json"


def _parse_common(request: Mapping[str, Any]) -> tuple:
    reject_forbidden_keys(request)

    if not isinstance(request, Mapping):
        raise SubtitleSkillError("INVALID_REQUEST", "request must be a JSON object")

    operation = request.get("operation")
    if operation not in SUPPORTED_OPERATIONS:
        raise SubtitleSkillError(
            "UNSUPPORTED_OPERATION", f"unsupported operation: {operation!r}; supported: {SUPPORTED_OPERATIONS}"
        )

    fmt = request.get("format")
    if fmt not in SUPPORTED_FORMATS:
        raise SubtitleSkillError("UNSUPPORTED_FORMAT", f"unsupported format: {fmt!r}; supported: {SUPPORTED_FORMATS}")

    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        raise SubtitleSkillError("INVALID_REQUEST", "request.workspace (absolute path) is required")

    output_path = request.get("output_path")
    if not isinstance(output_path, str) or not output_path:
        raise SubtitleSkillError("MISSING_INPUT", "request.output_path is required")

    subtitle_raw = request.get("subtitle")
    if subtitle_raw is None:
        raise SubtitleSkillError("MISSING_INPUT", "request.subtitle is required")
    document = SubtitleDocument.from_dict(subtitle_raw)

    constraints = SubtitleConstraints.from_dict(request.get("constraints"))

    return operation, fmt, workspace, output_path, document, constraints


def _write_text_exact(path: Path, content: str) -> None:
    """Write text without newline translation, portably on Python 3.9+.

    `Path.write_text(..., newline=...)` only exists from Python 3.10 (this
    package declares `requires-python = ">=3.9"`); `open(..., newline="")`
    has always supported it.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def _sidecar_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + _SIDECAR_SUFFIX)


def _try_reuse(output_path: Path, identity: str) -> Optional[dict]:
    sidecar = _sidecar_path(output_path)
    if not (output_path.exists() and sidecar.exists()):
        return None
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if recorded.get("identity") != identity:
        return None
    if not output_path.is_file() or output_path.stat().st_size == 0:
        return None
    actual_sha256 = sha256_file(output_path)
    if actual_sha256 != recorded.get("sha256"):
        return None
    return recorded


def _write_sidecar(output_path: Path, record: dict) -> None:
    _sidecar_path(output_path).write_text(canonical_json(record), encoding="utf-8")


def execute(request: Mapping[str, Any]) -> dict:
    started = time.monotonic()
    operation, fmt, workspace, output_path_rel, document, constraints = _parse_common(request)
    policy = PathPolicy(workspace)

    video_duration = request.get("video_duration")
    if video_duration is not None and (not isinstance(video_duration, (int, float)) or video_duration <= 0):
        raise SubtitleSkillError("INVALID_INPUT", "video_duration must be a positive number")

    issues = validate_document(document, constraints=constraints, video_duration=video_duration)
    document = document.sorted_by_start()

    if operation == "generate":
        return _run_generate(policy, document, fmt, output_path_rel, constraints, issues, started)
    return _run_render(policy, document, fmt, output_path_rel, constraints, issues, request, started)


def _identity_payload(document: SubtitleDocument, fmt: str, constraints: SubtitleConstraints, extra: dict) -> dict:
    payload = {
        "document": document.to_dict(),
        "format": fmt,
        "constraints": {k: v for k, v in constraints.__dict__.items() if v is not None},
    }
    payload.update(extra)
    return payload


def _run_generate(policy, document, fmt, output_path_rel, constraints, issues, started) -> dict:
    output_path = policy.resolve_output(output_path_rel)
    identity = compute_identity(
        skill_version=SKILL_VERSION,
        contract_version=CONTRACT_VERSION,
        operation="generate",
        payload=_identity_payload(document, fmt, constraints, {}),
    )

    reused = _try_reuse(output_path, identity)
    if reused is not None:
        return _finish(reused, output_path, issues, "generate", reused=True, started=started)

    content = GENERATORS[fmt](document)
    _write_text_exact(output_path, content)

    sha256 = sha256_file(output_path)
    record = {
        "identity": identity,
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "operation": "generate",
        "format": fmt,
        "sha256": sha256,
        "size": output_path.stat().st_size,
        "cue_count": len(document.cues),
    }
    _write_sidecar(output_path, record)
    return _finish(record, output_path, issues, "generate", reused=False, started=started)


def _document_wide_style_value(document: SubtitleDocument, field: str):
    """Collect the one value cue-level `style.<field>` must agree on across
    `document` to be forwarded as a single ffmpeg-skill/caption flag.

    Cues without a `style`, or with `style.<field>` left `None`, are simply
    not opinions about this field and are skipped. If every cue that *does*
    set `<field>` agrees, that value is returned (or `None` if no cue set
    it at all). If they disagree, this raises rather than picking one --
    caption.py's `--color`/`--bold`/`--size` apply to the whole burn, so a
    document that genuinely wants different values on different cues
    cannot be represented by a single `render` call at all, and silently
    honoring only one cue's request while dropping the others would
    misrepresent what the render actually did.
    """
    seen: dict = {}
    for cue in document.cues:
        if cue.style is None:
            continue
        value = getattr(cue.style, field)
        if value is None:
            continue
        seen.setdefault(value, []).append(cue.id)
    if len(seen) > 1:
        detail = ", ".join(f"{value!r} (cue {', '.join(cue_ids)})" for value, cue_ids in seen.items())
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"render burn cannot represent differing cue-level style.{field} values in a single "
            f"ffmpeg-skill/caption invocation (its --color/--bold/--size force_style applies to "
            f"the whole burn, not per cue): {detail}",
        )
    return next(iter(seen), None)


def _run_render(policy, document, fmt, output_path_rel, constraints, issues, request, started) -> dict:
    if fmt != "srt":
        raise SubtitleSkillError(
            "UNSUPPORTED_FORMAT",
            f"render only supports format='srt' (ffmpeg-skill/caption burns SRT or ASS, never {fmt!r})",
        )

    video_input_rel = request.get("video_input")
    if not isinstance(video_input_rel, str) or not video_input_rel:
        raise SubtitleSkillError("MISSING_INPUT", "request.video_input is required for the render operation")

    # request.mode selects ffmpeg-skill/caption's own --mode: "burn" (default,
    # renders pixels into the picture) or "mux" (copies video/audio untouched,
    # adds the SRT as a soft, player-toggleable subtitle stream). Validated
    # here, not left to caption.py's argparse `choices`, because an invalid
    # choices value there exits non-zero with a plain-text usage error on
    # stderr and no JSON at all -- this would otherwise surface as an opaque
    # DEPENDENCY_ERROR instead of a caller-actionable INVALID_INPUT.
    mode = request.get("mode", "burn")
    if mode not in engine_module.ALLOWED_RENDER_MODES:
        raise SubtitleSkillError(
            "INVALID_INPUT", f"request.mode must be one of {sorted(engine_module.ALLOWED_RENDER_MODES)}, got {mode!r}"
        )

    # request.audio_stream selects which 0-based audio track of a
    # multi-track input (dubbed languages, M&E stems) caption.py keeps
    # (--audio-stream N, ffmpeg-skill 0.12.0+). Only the type is validated
    # here; whether the value is actually in range for this video is
    # caption.py's own job (it probes the input and rejects an out-of-range
    # value as an INVALID_INPUT-mapped "kind": "input" failure).
    audio_stream = request.get("audio_stream")
    if audio_stream is not None and (
        isinstance(audio_stream, bool) or not isinstance(audio_stream, int) or audio_stream < 0
    ):
        raise SubtitleSkillError(
            "INVALID_INPUT", f"request.audio_stream must be a non-negative integer, got {audio_stream!r}"
        )

    # ffmpeg-skill/caption's --color/--bold/--size are a single, whole-burn
    # force_style setting -- there is no per-cue equivalent -- but
    # SubtitleStyle is a per-cue field on this skill's own document model
    # (kajisho5/subtitle-skill#5). Reduce it to the one document-wide value
    # each field will actually have, requiring every cue that sets a given
    # field to agree; a real disagreement is reported, never guessed at by
    # picking one cue's value and silently dropping the rest. Only
    # meaningful for mode="burn" -- caption.py's own docs say styling has no
    # effect on a soft-muxed stream, so mode="mux" does not even look.
    effective_style: Optional[SubtitleStyle] = None
    if mode == "burn":
        color = _document_wide_style_value(document, "color")
        bold = _document_wide_style_value(document, "bold")
        size = _document_wide_style_value(document, "size")
        if color is not None or bold is not None or size is not None:
            effective_style = SubtitleStyle(color=color, bold=bold, size=size)

    video_path = policy.resolve_input(video_input_rel)
    video_sha256 = sha256_file(video_path)
    output_path = policy.resolve_output(output_path_rel)

    # request.video_duration is an optional, caller-supplied hint used by
    # the generic validation in execute(); it may be absent or wrong. For
    # render we have the actual video in hand, so re-validate the document
    # against ffmpeg-skill's own probed duration -- otherwise a cue past
    # the real end of the video would render silently (libass just never
    # shows it) with no error and no observation, whenever the caller
    # omitted or mis-stated video_duration.
    ffmpeg_skill_root = resolve_ffmpeg_skill_root()
    if ffmpeg_skill_root is None:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill install not found (checked {engine_module.FFMPEG_SKILL_DIR_ENV} and the standard "
            "~/.claude, ~/.cursor, ~/.codex and ./.claude skills directories)",
        )
    real_video_duration = engine_module.probe(ffmpeg_skill_root, video_path).get("duration")
    issues = validate_document(document, constraints=constraints, video_duration=real_video_duration)

    # The render identity is anchored on the *content* of the ffmpeg-skill
    # scripts that will actually execute (sha256 of caption.py + _common.py),
    # not on ffmpeg-skill's self-reported package.json version: a version
    # string is only as trustworthy as whoever last edited it, and a
    # hand-patched caption.py with a stale package.json would otherwise
    # keep reporting the old version while behaving differently. The
    # content hash changes if and only if the code that will actually run
    # changes.
    engine_script_hash = ffmpeg_skill_script_hash(ffmpeg_skill_root)

    identity = compute_identity(
        skill_version=SKILL_VERSION,
        contract_version=CONTRACT_VERSION,
        operation="render",
        payload=_identity_payload(
            document,
            fmt,
            constraints,
            {
                "video_sha256": video_sha256,
                "engine_script_sha256": engine_script_hash,
                # mode/audio_stream change the actual caption.py argv (and
                # therefore the output) even for the same document/video, so
                # they must invalidate a cache hit like everything else here.
                "mode": mode,
                "audio_stream": audio_stream,
            },
        ),
    )

    reused = _try_reuse(output_path, identity)
    if reused is not None:
        return _finish(
            reused,
            output_path,
            issues,
            "render",
            reused=True,
            started=started,
            engine="ffmpeg-skill",
            engine_version=reused.get("engine_version"),
            # older sidecars predate the mode field; "burn" was the only
            # behavior that ever ran before it existed, so it is the honest
            # default rather than a fabricated guess.
            mode=reused.get("mode", "burn"),
        )

    # render's own SRT gets a narrow exemption from generate_srt's normal
    # "SRT can't represent this" rejection: for mode="burn", color/size are
    # actually conveyed by caption.py's own --color/--size flags below, not
    # by anything inside this SRT file, so rejecting them here would block
    # a render that will actually work. mode="mux" gets none of it (styling
    # has no effect there, so requesting it is worth rejecting up front,
    # not silently ignoring), and align/position/line are never exempted
    # for either mode -- burn has no equivalent for those either. See
    # formats.srt.generate_srt's docstring.
    allow_style_fields = frozenset({"color", "size"}) if mode == "burn" else frozenset()
    subtitle_content = generate_srt(document, allow_style_fields=allow_style_fields)
    subtitle_tmp_path = output_path.with_name(output_path.stem + f".subtitle-skill-src.{fmt}")
    _write_text_exact(subtitle_tmp_path, subtitle_content)

    try:
        engine_response = burn_in(
            video_path=video_path,
            subtitle_path=subtitle_tmp_path,
            subtitle_format=fmt,
            output_path=output_path,
            mode=mode,
            audio_stream=audio_stream,
            # SubtitleDocument.language is required and BCP47-validated by
            # models.py but was previously never read downstream; forwarded
            # here as caption.py's --mode mux language tag (--language is a
            # no-op for --mode burn, so it is only sent for mux -- see
            # engine.burn_in's docstring).
            language=document.language,
            # The document-wide color/bold/size reduction computed above
            # (None for mode="mux", or when no cue set any of the three).
            style=effective_style,
        )
    finally:
        subtitle_tmp_path.unlink(missing_ok=True)

    sha256 = sha256_file(output_path)
    record = {
        "identity": identity,
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "operation": "render",
        "format": fmt,
        "mode": mode,
        "audio_stream": audio_stream,
        "sha256": sha256,
        "size": output_path.stat().st_size,
        "cue_count": len(document.cues),
        "engine": "ffmpeg-skill",
        "engine_version": engine_response.get("engine_skill_version"),
        "engine_script_sha256": engine_response.get("engine_script_sha256"),
        "engine_response": {
            k: v
            for k, v in engine_response.items()
            if k not in ("status", "engine_skill_version", "engine_script_sha256")
        },
    }
    _write_sidecar(output_path, record)
    return _finish(
        record,
        output_path,
        issues,
        "render",
        reused=False,
        started=started,
        engine="ffmpeg-skill",
        engine_version=record["engine_version"],
        mode=mode,
    )


def _finish(
    record: dict,
    output_path: Path,
    issues,
    operation: str,
    *,
    reused: bool,
    started: float,
    engine: Optional[str] = None,
    engine_version: Optional[str] = None,
    mode: Optional[str] = None,
) -> dict:
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise SubtitleSkillError("OUTPUT_ERROR", "output file is missing or empty after execution")

    response = {
        "status": "ok",
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "output": str(output_path),
        "sha256": record["sha256"],
        "size": record["size"],
        "reused": reused,
        "observation": [i.to_dict() for i in issues],
        "timeline": {
            "cue_count": record.get("cue_count"),
        },
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
    }
    if engine is not None:
        response["engine"] = engine
        response["engine_version"] = engine_version
        response["mode"] = mode
    return response
