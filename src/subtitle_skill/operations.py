"""Operation execution: generate / render / translate / mux.

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
from .engine import burn_in, ffmpeg_skill_script_hash, mux_tracks, resolve_ffmpeg_skill_root
from .errors import SubtitleSkillError
from .formats import GENERATORS, SUPPORTED_FORMATS, generate_srt
from .locales import profile_for
from .models import SubtitleDocument, SubtitleStyle, _is_bcp47_ish
from .pathpolicy import PathPolicy
from .provenance import canonical_json, compute_identity, sha256_file
from .security import reject_forbidden_keys
from .validation import (
    DEFAULT_MAX_CHARS_PER_LINE,
    DEFAULT_MAX_LINES,
    SubtitleConstraints,
    ValidationIssue,
    validate_document,
)

SUPPORTED_OPERATIONS = ("generate", "render", "translate", "mux")

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
    reject_forbidden_keys(request)
    if isinstance(request, Mapping) and request.get("operation") == "translate":
        return _run_translate(request, started)
    if isinstance(request, Mapping) and request.get("operation") == "mux":
        return _run_mux(request, started)
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


# --------------------------------------------------------------------------
# translate (ECOSYSTEM_ROADMAP step 5, docs/adr/ADR-004-translation-engine.md)
# --------------------------------------------------------------------------

_TRANSLATE_KEYS = frozenset(
    {
        "operation",
        "workspace",
        "subtitle",
        "target_language",
        "format",
        "output_path",
        "engine",
        "glossary",
        "translation_memory",
        "constraints",
        "video_duration",
    }
)


def _require_workspace_and_output(request: Mapping[str, Any]) -> tuple:
    workspace = request.get("workspace")
    if not isinstance(workspace, str) or not workspace:
        raise SubtitleSkillError("INVALID_REQUEST", "request.workspace (absolute path) is required")
    output_path = request.get("output_path")
    if not isinstance(output_path, str) or not output_path:
        raise SubtitleSkillError("MISSING_INPUT", "request.output_path is required")
    return workspace, output_path


def _check_video_duration(request: Mapping[str, Any]):
    video_duration = request.get("video_duration")
    if video_duration is not None and (
        isinstance(video_duration, bool) or not isinstance(video_duration, (int, float)) or video_duration <= 0
    ):
        raise SubtitleSkillError("INVALID_INPUT", "video_duration must be a positive number")
    return video_duration


def _run_translate(request: Mapping[str, Any], started: float) -> dict:
    # imported here so `generate`/`render` never load the translation module
    from . import translation

    unknown = set(request) - _TRANSLATE_KEYS
    if unknown:
        raise SubtitleSkillError("INVALID_REQUEST", f"unknown translate request field(s): {sorted(unknown)}")
    fmt = request.get("format")
    if fmt not in SUPPORTED_FORMATS:
        raise SubtitleSkillError("UNSUPPORTED_FORMAT", f"unsupported format: {fmt!r}; supported: {SUPPORTED_FORMATS}")
    workspace, output_path_rel = _require_workspace_and_output(request)
    if request.get("subtitle") is None:
        raise SubtitleSkillError("MISSING_INPUT", "request.subtitle is required")
    source = SubtitleDocument.from_dict(request["subtitle"])

    target = request.get("target_language")
    if target is None:
        raise SubtitleSkillError("MISSING_INPUT", "request.target_language is required")
    if not isinstance(target, str) or not _is_bcp47_ish(target):
        raise SubtitleSkillError("INVALID_INPUT", f"target_language is not a valid language tag: {target!r}")
    if target.split("-", 1)[0].lower() == source.language.split("-", 1)[0].lower():
        raise SubtitleSkillError(
            "INVALID_INPUT", f"target_language {target!r} is the source document's language ({source.language!r})"
        )

    engine_name = request.get("engine", translation.DEFAULT_ENGINE)
    if engine_name not in translation.ENGINES:
        raise SubtitleSkillError("INVALID_INPUT", f"engine must be one of {list(translation.ENGINES)}, got {engine_name!r}")
    glossary = translation.parse_glossary(request.get("glossary"))
    memory = translation.parse_memory(request.get("translation_memory"))
    if memory and engine_name != "memory":
        raise SubtitleSkillError("INVALID_INPUT", "translation_memory is only read by engine 'memory'")
    if engine_name == "memory" and not memory:
        raise SubtitleSkillError("MISSING_INPUT", "engine 'memory' needs a non-empty translation_memory")
    constraints = SubtitleConstraints.from_dict(request.get("constraints"))
    video_duration = _check_video_duration(request)

    policy = PathPolicy(workspace)
    output_path = policy.resolve_output(output_path_rel)

    # The source must be a valid document in its own right (fatal problems
    # raise here); its warnings belong to the source language and are not
    # reported -- the translated document is validated below.
    validate_document(source, video_duration=video_duration)
    source = source.sorted_by_start()

    engine = translation.make_engine(engine_name, memory=memory)
    if engine_name == "argos":
        engine_version = engine.pair_version(source.language, target)
    else:
        engine_version = engine.version()

    target_profile = profile_for(target)
    max_chars = constraints.max_chars_per_line or (
        target_profile.max_chars_per_line if target_profile is not None else DEFAULT_MAX_CHARS_PER_LINE
    )
    max_lines = constraints.max_lines or DEFAULT_MAX_LINES

    identity = compute_identity(
        skill_version=SKILL_VERSION,
        contract_version=CONTRACT_VERSION,
        operation="translate",
        payload=_identity_payload(
            source,
            fmt,
            constraints,
            {
                "target_language": target,
                "engine": engine_name,
                "engine_version": engine_version,
                "glossary": [[e.source, e.target] for e in glossary],
                "video_duration": video_duration,
            },
        ),
    )

    reused = _try_reuse(output_path, identity)
    if reused is not None and isinstance(reused.get("document"), Mapping):
        translated = SubtitleDocument.from_dict(reused["document"])
        misses = [tuple(m) for m in reused.get("glossary_misses", [])]
        issues = _translate_issues(translated, constraints, video_duration, misses)
        response = _finish(reused, output_path, issues, "translate", reused=True, started=started)
        return _translate_response(response, reused)

    result = translation.translate_document(
        source,
        target_language=target,
        engine=engine,
        glossary=glossary,
        max_chars_per_line=max_chars,
        max_lines=max_lines,
        engine_version=engine_version,
    )
    translated = result.document
    misses = [(cue_id, entry.source, entry.target) for cue_id, entry in result.glossary_misses]
    issues = _translate_issues(translated, constraints, video_duration, misses)

    content = GENERATORS[fmt](translated)
    _write_text_exact(output_path, content)
    sha256 = sha256_file(output_path)
    record = {
        "identity": identity,
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "operation": "translate",
        "format": fmt,
        "sha256": sha256,
        "size": output_path.stat().st_size,
        "cue_count": len(translated.cues),
        "engine": result.engine,
        "engine_version": result.engine_version,
        "source_language": source.language,
        "target_language": target,
        "glossary_misses": [list(m) for m in misses],
        "document": translated.to_dict(),
    }
    _write_sidecar(output_path, record)
    response = _finish(record, output_path, issues, "translate", reused=False, started=started)
    return _translate_response(response, record)


def _translate_issues(translated, constraints, video_duration, misses) -> list:
    """The translated document validated with the TARGET language's locale
    profile, plus one GLOSSARY_TERM_MISSING warning per missed term."""
    issues = list(validate_document(translated, constraints=constraints, video_duration=video_duration))
    for cue_id, src, tgt in misses:
        issues.append(
            ValidationIssue(
                "warning",
                "GLOSSARY_TERM_MISSING",
                cue_id,
                f"glossary term {src!r} is in the source but its target {tgt!r} is not in the translation",
            )
        )
    return issues


def _translate_response(response: dict, record: Mapping[str, Any]) -> dict:
    response["engine"] = record.get("engine")
    response["engine_version"] = record.get("engine_version")
    response["source_language"] = record.get("source_language")
    response["target_language"] = record.get("target_language")
    response["subtitle"] = record.get("document")
    return response


# --------------------------------------------------------------------------
# mux (ECOSYSTEM_ROADMAP step 6): several documents -> one video
# --------------------------------------------------------------------------

_MUX_KEYS = frozenset(
    {
        "operation",
        "workspace",
        "video_input",
        "output_path",
        "tracks",
        "format",
        "default_language",
        "audio_stream",
        "constraints",
    }
)
_MUX_TRACK_KEYS = frozenset({"subtitle", "title"})
MUX_MAX_TRACKS = 32


def _mux_language(document: SubtitleDocument) -> str:
    """The document's language as caption.py's `--srt FILE:LANG` accepts it
    (primary subtag lower-cased), or INVALID_INPUT."""
    parts = document.language.split("-")
    tag = "-".join([parts[0].lower()] + parts[1:])
    if not engine_module.MUX_LANGUAGE_RE.match(tag):
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"document {document.id!r}: language {document.language!r} cannot tag a muxed track (needs a 2-3 letter "
            "primary subtag, optionally followed by 2-8 character subtags, e.g. en, ja, pt-BR)",
        )
    return tag


def _run_mux(request: Mapping[str, Any], started: float) -> dict:
    unknown = set(request) - _MUX_KEYS
    if unknown:
        raise SubtitleSkillError("INVALID_REQUEST", f"unknown mux request field(s): {sorted(unknown)}")
    fmt = request.get("format", "srt")
    if fmt != "srt":
        raise SubtitleSkillError(
            "UNSUPPORTED_FORMAT",
            f"mux only supports format='srt' (ffmpeg-skill/caption muxes SRT, never {fmt!r}; it refuses ASS for mux)",
        )
    workspace, output_path_rel = _require_workspace_and_output(request)
    video_input_rel = request.get("video_input")
    if not isinstance(video_input_rel, str) or not video_input_rel:
        raise SubtitleSkillError("MISSING_INPUT", "request.video_input is required for the mux operation")

    raw_tracks = request.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise SubtitleSkillError("MISSING_INPUT", "request.tracks must be a non-empty array of {subtitle, title?}")
    if len(raw_tracks) > MUX_MAX_TRACKS:
        raise SubtitleSkillError("INVALID_INPUT", f"mux takes at most {MUX_MAX_TRACKS} tracks")
    documents = []
    titles = []
    for i, raw in enumerate(raw_tracks):
        if not isinstance(raw, Mapping):
            raise SubtitleSkillError("INVALID_INPUT", f"tracks[{i}] must be an object")
        extra = set(raw) - _MUX_TRACK_KEYS
        if extra:
            raise SubtitleSkillError("INVALID_INPUT", f"tracks[{i}]: unknown field(s) {sorted(extra)}")
        if raw.get("subtitle") is None:
            raise SubtitleSkillError("MISSING_INPUT", f"tracks[{i}].subtitle is required")
        title = raw.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip() or "\n" in title):
            raise SubtitleSkillError("INVALID_INPUT", f"tracks[{i}].title must be a non-empty single-line string")
        documents.append(SubtitleDocument.from_dict(raw["subtitle"]))
        titles.append(title)
    if any(t is not None for t in titles) and not all(t is not None for t in titles):
        # caption.py pairs --track-title with --srt by position, so a title for
        # some tracks only cannot be expressed without inventing the others'.
        raise SubtitleSkillError("INVALID_INPUT", "give a title for every track or for none")

    languages = [_mux_language(doc) for doc in documents]
    lowered = [lang.lower() for lang in languages]
    dup = sorted({lang for lang in lowered if lowered.count(lang) > 1})
    if dup:
        raise SubtitleSkillError(
            "INVALID_INPUT", f"two tracks share the language {', '.join(dup)}; a player cannot tell them apart"
        )
    default_language = request.get("default_language")
    if default_language is not None:
        if not isinstance(default_language, str) or default_language.lower() not in lowered:
            raise SubtitleSkillError(
                "INVALID_INPUT", f"default_language {default_language!r} is not the language of any track {languages}"
            )
        default_language = languages[lowered.index(default_language.lower())]

    audio_stream = request.get("audio_stream")
    if audio_stream is not None and (
        isinstance(audio_stream, bool) or not isinstance(audio_stream, int) or audio_stream < 0
    ):
        raise SubtitleSkillError(
            "INVALID_INPUT", f"request.audio_stream must be a non-negative integer, got {audio_stream!r}"
        )
    constraints = SubtitleConstraints.from_dict(request.get("constraints"))

    policy = PathPolicy(workspace)
    video_path = policy.resolve_input(video_input_rel)
    output_path = policy.resolve_output(output_path_rel)
    if output_path.suffix.lower() not in engine_module.MUX_CONTAINERS:
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"mux output must be one of {sorted(engine_module.MUX_CONTAINERS)}, got {output_path.suffix or 'no extension'!r}",
        )
    video_sha256 = sha256_file(video_path)

    ffmpeg_skill_root = resolve_ffmpeg_skill_root()
    if ffmpeg_skill_root is None:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill install not found (checked {engine_module.FFMPEG_SKILL_DIR_ENV} and the standard "
            "~/.claude, ~/.cursor, ~/.codex and ./.claude skills directories)",
        )
    real_video_duration = engine_module.probe(ffmpeg_skill_root, video_path).get("duration")

    issues = []
    sorted_docs = []
    for doc, lang in zip(documents, languages):
        try:
            track_issues = validate_document(doc, constraints=constraints, video_duration=real_video_duration)
        except SubtitleSkillError as exc:
            if exc.code != "VALIDATION_ERROR" or not isinstance(exc.details, list):
                raise
            details = [
                dict(d, cue_id=f"{lang}:{d['cue_id']}") if isinstance(d, dict) and "cue_id" in d else d
                for d in exc.details
            ]
            raise SubtitleSkillError(
                "VALIDATION_ERROR", f"track {lang!r} (document {doc.id!r}): {exc.message}", details=details
            ) from exc
        for issue in track_issues:
            d = issue
            # prefix the cue id with the track language so two tracks' observations stay apart
            issues.append(
                ValidationIssue(d.severity, d.code, f"{lang}:{d.cue_id}" if d.cue_id else lang, d.message, d.suggested_text)
            )
        sorted_docs.append(doc.sorted_by_start())

    engine_script_hash = ffmpeg_skill_script_hash(ffmpeg_skill_root)
    identity = compute_identity(
        skill_version=SKILL_VERSION,
        contract_version=CONTRACT_VERSION,
        operation="mux",
        payload={
            "tracks": [{"document": d.to_dict(), "title": t} for d, t in zip(sorted_docs, titles)],
            "format": fmt,
            "constraints": {k: v for k, v in constraints.__dict__.items() if v is not None},
            "default_language": default_language,
            "audio_stream": audio_stream,
            "video_sha256": video_sha256,
            "engine_script_sha256": engine_script_hash,
        },
    )

    reused = _try_reuse(output_path, identity)
    if reused is not None:
        issues += [ValidationIssue(**o) for o in reused.get("engine_observations", [])]
        response = _finish(
            reused, output_path, issues, "mux", reused=True, started=started,
            engine="ffmpeg-skill", engine_version=reused.get("engine_version"), mode="mux",
        )
        response["tracks"] = reused.get("tracks")
        return response

    srt_paths = []
    try:
        for n, (doc, lang) in enumerate(zip(sorted_docs, languages)):
            path = output_path.with_name(f"{output_path.stem}.subtitle-skill-src.{n}.{lang}.srt")
            _write_text_exact(path, generate_srt(doc))
            srt_paths.append(path)
        engine_response = mux_tracks(
            video_path=video_path,
            tracks=[(p, lang, t) for p, lang, t in zip(srt_paths, languages, titles)],
            output_path=output_path,
            audio_stream=audio_stream,
            default_language=default_language,
        )
    finally:
        for path in srt_paths:
            path.unlink(missing_ok=True)

    reported = [t for t in engine_response.get("tracks") or [] if isinstance(t, dict) and not t.get("kept_from_input")]
    # caption.py lays out the FIRST --srt for the video frame (wrap to the safe
    # width) and, when that changes its lines, muxes an adjusted copy instead.
    # --min-duration 0 / a high --max-lines keep its timing; the line breaks
    # cannot be protected (caption.py has no switch for it), so say so.
    engine_observations = []
    for i, (path, lang) in enumerate(zip(srt_paths, languages)):
        muxed = reported[i].get("file") if i < len(reported) else None
        if isinstance(muxed, str) and Path(muxed).name != path.name:
            engine_observations.append(
                {
                    "severity": "warning",
                    "code": "TRACK_RELAID_OUT",
                    "cue_id": lang,
                    "message": (
                        f"ffmpeg-skill/caption re-wrapped this track's lines for the video frame and muxed its "
                        f"adjusted copy {Path(muxed).name} (timing unchanged; its layout pass has no off switch)"
                    ),
                }
            )
    issues += [ValidationIssue(**o) for o in engine_observations]
    tracks_out = []
    for i, (doc, lang, title) in enumerate(zip(sorted_docs, languages, titles)):
        info = reported[i] if i < len(reported) else {}
        tracks_out.append(
            {
                "document_id": doc.id,
                "language": doc.language,
                "stored_language": info.get("language", lang),
                "title": info.get("title", title),
                "default": bool(info.get("default", default_language == lang)),
                "cue_count": len(doc.cues),
                "codec": info.get("codec", engine_response.get("codec")),
            }
        )

    sha256 = sha256_file(output_path)
    record = {
        "identity": identity,
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "operation": "mux",
        "format": fmt,
        "mode": "mux",
        "audio_stream": audio_stream,
        "sha256": sha256,
        "size": output_path.stat().st_size,
        "cue_count": sum(len(d.cues) for d in sorted_docs),
        "tracks": tracks_out,
        "engine_observations": engine_observations,
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
    response = _finish(
        record, output_path, issues, "mux", reused=False, started=started,
        engine="ffmpeg-skill", engine_version=record["engine_version"], mode="mux",
    )
    response["tracks"] = tracks_out
    return response
