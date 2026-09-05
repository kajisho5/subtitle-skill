"""Delegation to the execution skill that owns actual media processing.

subtitle-skill NEVER builds an FFmpeg command line, filter graph, or shell
string itself. Burn-in rendering is delegated to an external execution
skill (ffmpeg-skill) through that skill's own typed `run - --json`
process-boundary contract, invoked as a fixed argv list (no shell=True,
no user-controlled executable/argv/filter/env).

The exact ffmpeg-skill request/response shape is an *assumption* here
(this repository does not have access to the ffmpeg-skill source to
confirm its contract) -- see README "ffmpeg-skill integration" for the
documented shape and how to adjust `_build_request`/`_parse_response` if
the real contract differs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .errors import SubtitleSkillError

#: Name of the execution skill binary. Overridable only via environment
#: configuration (never from a request payload) for local development.
FFMPEG_SKILL_BIN_ENV = "SUBTITLE_SKILL_FFMPEG_SKILL_BIN"
DEFAULT_FFMPEG_SKILL_BIN = "ffmpeg-skill"


def resolve_ffmpeg_skill_bin() -> Optional[str]:
    configured = os.environ.get(FFMPEG_SKILL_BIN_ENV, DEFAULT_FFMPEG_SKILL_BIN)
    return shutil.which(configured)


def is_available() -> bool:
    return resolve_ffmpeg_skill_bin() is not None


def burn_in(
    *,
    video_path: Path,
    subtitle_path: Path,
    subtitle_format: str,
    output_path: Path,
    timeout_seconds: int = 600,
) -> dict:
    """Delegate subtitle burn-in to ffmpeg-skill. Returns ffmpeg-skill's
    parsed JSON response. Raises SubtitleSkillError(DEPENDENCY_ERROR) if
    the execution skill is unavailable or fails.
    """
    binary = resolve_ffmpeg_skill_bin()
    if binary is None:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"execution skill not found on PATH (looked for '{os.environ.get(FFMPEG_SKILL_BIN_ENV, DEFAULT_FFMPEG_SKILL_BIN)}')",
        )

    request = {
        "operation": "burn_in_subtitles",
        "input_video": str(video_path),
        "subtitle_file": str(subtitle_path),
        "subtitle_format": subtitle_format,
        "output_video": str(output_path),
    }

    try:
        proc = subprocess.run(
            [binary, "run", "-", "--json"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"execution skill timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"failed to launch execution skill: {exc}") from exc

    if proc.returncode != 0:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"execution skill exited with code {proc.returncode}: {proc.stderr.strip()[:2000]}",
        )

    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", "execution skill returned malformed JSON") from exc

    if not isinstance(response, dict) or response.get("status") != "ok":
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR", f"execution skill reported failure: {response!r}"
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise SubtitleSkillError("OUTPUT_ERROR", "execution skill reported success but output is missing/empty")

    return response
