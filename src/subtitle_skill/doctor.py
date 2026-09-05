"""`doctor --json`: reports only operations that can actually execute now."""
from __future__ import annotations

from . import CONTRACT_VERSION, SKILL_ID, SKILL_VERSION
from .contract import CONTRACT_VERSION as _CV  # noqa: F401 (re-export sanity)
from .engine import is_available, resolve_ffmpeg_skill_bin, DEFAULT_FFMPEG_SKILL_BIN, FFMPEG_SKILL_BIN_ENV
from .formats import SUPPORTED_FORMATS
import os


def build_doctor_report() -> dict:
    ffmpeg_skill_available = is_available()
    problems = []
    if not ffmpeg_skill_available:
        configured = os.environ.get(FFMPEG_SKILL_BIN_ENV, DEFAULT_FFMPEG_SKILL_BIN)
        problems.append(
            {
                "code": "DEPENDENCY_ERROR",
                "message": f"execution skill '{configured}' not found on PATH; 'render' operation is unavailable",
            }
        )

    available_operations = ["generate"]
    if ffmpeg_skill_available:
        available_operations.append("render")

    return {
        "skill": SKILL_ID,
        "version": SKILL_VERSION,
        "contract_version": CONTRACT_VERSION,
        "supported_operations": available_operations,
        "supported_formats": list(SUPPORTED_FORMATS),
        "dependencies": {
            "ffmpeg-skill": {
                "available": ffmpeg_skill_available,
                "resolved_path": resolve_ffmpeg_skill_bin(),
            }
        },
        "problems": problems,
        "healthy": not problems,
    }
