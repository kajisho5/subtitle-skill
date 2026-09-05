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
from .engine import burn_in, ffmpeg_skill_version, resolve_ffmpeg_skill_root
from .errors import SubtitleSkillError
from .formats import GENERATORS, SUPPORTED_FORMATS
from .models import SubtitleDocument
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
    output_path.write_text(content, encoding="utf-8", newline="")

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


def _run_render(policy, document, fmt, output_path_rel, constraints, issues, request, started) -> dict:
    if fmt != "srt":
        raise SubtitleSkillError(
            "UNSUPPORTED_FORMAT",
            f"render only supports format='srt' (ffmpeg-skill/caption burns SRT or ASS, never {fmt!r})",
        )

    video_input_rel = request.get("video_input")
    if not isinstance(video_input_rel, str) or not video_input_rel:
        raise SubtitleSkillError("MISSING_INPUT", "request.video_input is required for the render operation")

    video_path = policy.resolve_input(video_input_rel)
    video_sha256 = sha256_file(video_path)
    output_path = policy.resolve_output(output_path_rel)

    # ffmpeg-skill's own version is part of the render identity: a different
    # ffmpeg-skill build can change encoder defaults/output bytes for the
    # same inputs, so a cached render from a since-upgraded ffmpeg-skill
    # must not be reused as if nothing changed.
    ffmpeg_skill_root = resolve_ffmpeg_skill_root()
    engine_version = ffmpeg_skill_version(ffmpeg_skill_root) if ffmpeg_skill_root else None

    identity = compute_identity(
        skill_version=SKILL_VERSION,
        contract_version=CONTRACT_VERSION,
        operation="render",
        payload=_identity_payload(
            document, fmt, constraints, {"video_sha256": video_sha256, "engine_version": engine_version}
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
        )

    subtitle_content = GENERATORS[fmt](document)
    subtitle_tmp_path = output_path.with_name(output_path.stem + f".subtitle-skill-src.{fmt}")
    subtitle_tmp_path.write_text(subtitle_content, encoding="utf-8", newline="")

    try:
        engine_response = burn_in(
            video_path=video_path,
            subtitle_path=subtitle_tmp_path,
            subtitle_format=fmt,
            output_path=output_path,
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
        "sha256": sha256,
        "size": output_path.stat().st_size,
        "cue_count": len(document.cues),
        "engine": "ffmpeg-skill",
        "engine_version": engine_response.get("engine_skill_version"),
        "engine_response": {k: v for k, v in engine_response.items() if k not in ("status", "engine_skill_version")},
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
    return response
