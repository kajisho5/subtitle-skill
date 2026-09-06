"""Machine-readable capability contract (`contract --json`)."""
from __future__ import annotations

from . import CONTRACT_VERSION, SKILL_ID, SKILL_VERSION
from .doctor import RENDER_SUPPORTED_FORMATS
from .errors import ERROR_CODES, NON_RETRYABLE_CODES
from .formats import SUPPORTED_FORMATS

OPERATIONS = ("generate", "render")

# Cross-repository Capability ids (kajisho5/AI-video-production-OS docs/SPEC.md
# `CapabilityContract.provides`), so a registry can resolve "who provides
# subtitle.generate" without hardcoding this repository. Unlike video-editing-skill's
# `operations.OPERATIONS`, this repo's own OPERATIONS tuple carries no capability-shaped
# id today, so this is a new naming decision, not a mechanical derivation - it matches
# the id already assigned to these two operations in that project's own
# docs/CAPABILITY_MATRIX.md, kept here as the single source of truth going forward.
PROVIDES = {
    "generate": "subtitle.generate",
    "render": "subtitle.render",
}


def build_contract() -> dict:
    return {
        "skill_id": SKILL_ID,
        "version": SKILL_VERSION,
        "contract_version": CONTRACT_VERSION,
        "deterministic": True,
        "operations": {
            "generate": {
                "description": "Validate a typed subtitle document and produce a subtitle file (no video I/O).",
                "inputs": ["subtitle", "format", "output_path", "constraints?"],
                "outputs": ["output", "sha256", "size", "timeline", "observation"],
                "formats": list(SUPPORTED_FORMATS),
            },
            "render": {
                "description": (
                    "Burn a subtitle document into a video by delegating to ffmpeg-skill's "
                    "`caption` tool (scripts/caption.py). format must be 'srt': ffmpeg-skill's "
                    "caption tool burns SRT or ASS, never WebVTT."
                ),
                "inputs": ["video_input", "subtitle", "format", "output_path", "constraints?"],
                "outputs": ["output", "sha256", "size", "timeline", "observation", "engine"],
                "formats": list(RENDER_SUPPORTED_FORMATS),
                "delegates_to": {"skill_id": "ffmpeg-skill", "tool": "caption"},
            },
        },
        "provides": [
            {"id": PROVIDES[op], "lifecycle": "EXPERIMENTAL", "tool_id": f"{SKILL_ID}/{op}"}
            for op in OPERATIONS
        ],
        "capabilities": {
            "formats": list(SUPPORTED_FORMATS),
            "render_formats": list(RENDER_SUPPORTED_FORMATS),
            "path_policy": True,
            "provenance": True,
            "reuse": True,
        },
        "parameters": {
            "constraints": [
                "max_chars_per_line",
                "max_lines",
                "min_duration",
                "max_duration",
                "reading_speed_cps",
            ],
            "style": ["align", "position", "line", "size", "bold", "italic", "color"],
        },
        "errors": {
            "codes": sorted(ERROR_CODES),
            "non_retryable": sorted(NON_RETRYABLE_CODES),
        },
        "out_of_scope": [
            "speech_recognition",
            "transcription",
            "speaker_diarization",
            "ai_editing_decisions",
            "semantic_editing",
            "production_decision",
            "arbitrary_ffmpeg_execution",
        ],
    }
