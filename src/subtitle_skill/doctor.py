"""`doctor --json`: reports only operations that can actually execute now.

`render` requires ffmpeg-skill's `caption` tool. Per ffmpeg-skill's own
contract (`scripts/_contract.py` TOOL_META["caption"]), that tool's
required capabilities are `ffmpeg`, `ffprobe`, `encoder:libx264`,
`encoder:aac` and `filter:subtitles`. Rather than re-implementing ffmpeg
capability detection here (a second, possibly-drifting copy of logic
ffmpeg-skill already owns), this module asks ffmpeg-skill's own
`scripts/_contract.py doctor --json` for that verdict once the install is
located, and folds it into subtitle-skill's own report.
"""
from __future__ import annotations

import json
import subprocess
import sys

from . import CONTRACT_VERSION, SKILL_ID, SKILL_VERSION
from .engine import FFMPEG_SKILL_DIR_ENV, resolve_ffmpeg_skill_root
from .formats import SUPPORTED_FORMATS

#: Capabilities ffmpeg-skill's `caption` tool always requires, per its own
#: contract (scripts/_contract.py TOOL_META["caption"]["required"]).
_CAPTION_REQUIRED_CAPABILITIES = ("ffmpeg", "ffprobe", "encoder:libx264", "encoder:aac", "filter:subtitles")

#: `render` only ever produces SRT-driven burn-ins; ffmpeg-skill's
#: `caption.py` has no WebVTT support (`--srt` / `--ass` only).
RENDER_SUPPORTED_FORMATS = ("srt",)


def _ffmpeg_skill_capabilities(root) -> dict:
    """Run ffmpeg-skill's own doctor and check the capabilities `caption` needs.

    Returns {"checked": bool, "missing": [...], "unknown": [...], "detail": ...}.
    Never raises: a failure here is reported as a problem, not a crash.
    """
    script = root / "scripts" / "_contract.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        report = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"checked": False, "missing": [], "unknown": [], "detail": f"ffmpeg-skill doctor failed: {exc}"}

    # ffmpeg-skill's own doctor already classifies each capability as
    # available / missing / unknown (a listing it could not prove one way
    # or the other -- never folded into "missing" there, so not here either).
    missing = sorted(set(report.get("missing", [])) & set(_CAPTION_REQUIRED_CAPABILITIES))
    unknown = sorted(set(report.get("unknown", [])) & set(_CAPTION_REQUIRED_CAPABILITIES))
    return {"checked": True, "missing": missing, "unknown": unknown, "detail": None}


def build_doctor_report() -> dict:
    root = resolve_ffmpeg_skill_root()
    problems = []
    render_available = False
    ffmpeg_skill_dependency = {"available": False, "resolved_path": None, "capabilities": None}

    if root is None:
        problems.append(
            {
                "code": "DEPENDENCY_ERROR",
                "message": (
                    f"ffmpeg-skill install not found (checked {FFMPEG_SKILL_DIR_ENV} and the standard "
                    "~/.claude, ~/.cursor, ~/.codex and ./.claude skills directories); "
                    "'render' operation is unavailable"
                ),
            }
        )
    else:
        ffmpeg_skill_dependency["resolved_path"] = str(root)
        caps = _ffmpeg_skill_capabilities(root)
        ffmpeg_skill_dependency["capabilities"] = caps
        if not caps["checked"]:
            problems.append(
                {"code": "DEPENDENCY_ERROR", "message": f"could not verify ffmpeg-skill capabilities: {caps['detail']}"}
            )
        elif caps["missing"]:
            problems.append(
                {
                    "code": "DEPENDENCY_ERROR",
                    "message": f"ffmpeg-skill is installed but missing required capabilities for caption: {caps['missing']}",
                }
            )
        else:
            # "unknown" capabilities (detection could not confirm, but did
            # not prove missing either) are a warning, not a blocker: render
            # stays available, same as ffmpeg-skill's own doctor semantics.
            render_available = True
            ffmpeg_skill_dependency["available"] = True
            if caps["unknown"]:
                problems.append(
                    {
                        "code": "DEPENDENCY_ERROR",
                        "severity": "warning",
                        "message": f"ffmpeg-skill could not confirm these capabilities (not proven missing): {caps['unknown']}",
                    }
                )

    available_operations = ["generate"]
    if render_available:
        available_operations.append("render")

    return {
        "skill": SKILL_ID,
        "version": SKILL_VERSION,
        "contract_version": CONTRACT_VERSION,
        "supported_operations": available_operations,
        "supported_formats": list(SUPPORTED_FORMATS),
        "render_supported_formats": list(RENDER_SUPPORTED_FORMATS),
        "dependencies": {"ffmpeg-skill": ffmpeg_skill_dependency},
        "problems": problems,
        "healthy": not any(p.get("severity", "blocking") == "blocking" for p in problems),
    }
