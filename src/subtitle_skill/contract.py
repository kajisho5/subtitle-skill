"""Machine-readable capability contract (`contract --json`)."""
from __future__ import annotations

from . import CONTRACT_VERSION, SKILL_ID, SKILL_VERSION
from .doctor import RENDER_SUPPORTED_FORMATS
from .engine import ALLOWED_RENDER_MODES, FFMPEG_SKILL_CONTRACT_VERSION, FFMPEG_SKILL_TOOLS, MUX_CONTAINERS
from .errors import ERROR_CODES, NON_RETRYABLE_CODES
from .formats import SUPPORTED_FORMATS
from .translation import DEFAULT_ENGINE, ENGINES

OPERATIONS = ("generate", "render", "translate", "mux")

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
    # AI-video-production-OS docs/DELIVERY_AND_INTEROP.md section 8 names this Capability.
    "translate": "subtitle.translate",
    "mux": "subtitle.mux",
}

# Prefix of every `provides[].tool_id` (docs/adr/ADR-002-provides-tool-id-prefix.md). It is
# the short domain name, not SKILL_ID: thumbnail-skill (`thumbnail/...`) and
# transcription-skill (`transcription/...`) publish the same way, and it is the package id
# video-production-agent registers this Skill under (`subtitle/generate`, `subtitle/render`).
# SKILL_ID stays "subtitle-skill"; only the tool ids use this prefix.
TOOL_ID_PREFIX = "subtitle"



def dependencies() -> list:
    """The AI Video Production OS registry's `dependencies` (kajisho5/AI-video-production-OS
    registry/capability_contract.schema.json): ffmpeg-skill, as a range of its contract_version,
    in the same shape audio-production-skill, color-grading-skill and motion-graphics-skill
    publish. A lower bound only, derived from engine.FFMPEG_SKILL_CONTRACT_VERSION so the
    contract and the code that delegates can never say different things
    (docs/adr/ADR-003-single-source-version.md). Not required: `generate` never touches it.
    """
    return [
        {
            "skill_id": "ffmpeg-skill",
            "version_range": f">={FFMPEG_SKILL_CONTRACT_VERSION}",
            "range_of": "contract_version",
            "required": False,
            "for": [f"{TOOL_ID_PREFIX}/render", f"{TOOL_ID_PREFIX}/mux"],
            "tools": [f"ffmpeg-skill/{tool}" for tool in FFMPEG_SKILL_TOOLS],
        }
    ]


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
                    "Burn a subtitle document into a video, or mux it in as a soft "
                    "(player-toggleable) subtitle stream, by delegating to ffmpeg-skill's "
                    "`caption` tool (scripts/caption.py). format must be 'srt': ffmpeg-skill's "
                    "caption tool burns SRT or ASS, never WebVTT. `mode: \"mux\"` copies the "
                    "video/audio streams untouched and forwards `subtitle.language` as the new "
                    "subtitle stream's language tag; `audio_stream` selects which 0-based audio "
                    "track of a multi-track input is kept, for either mode. For `mode: \"burn\"` "
                    "only, cue `style.color`/`style.bold`/`style.size` are forwarded to "
                    "caption.py's own `--color`/`--bold`/`--size` (a single, whole-burn setting, "
                    "so every cue that sets one of these fields must agree on its value); "
                    "`style.align`/`style.position`/`style.line`/`style.italic` have no "
                    "caption.py equivalent and are never forwarded."
                ),
                "inputs": [
                    "video_input",
                    "subtitle",
                    "format",
                    "output_path",
                    "constraints?",
                    "mode?",
                    "audio_stream?",
                ],
                "outputs": ["output", "sha256", "size", "timeline", "observation", "engine", "mode"],
                "formats": list(RENDER_SUPPORTED_FORMATS),
                "delegates_to": {"skill_id": "ffmpeg-skill", "tool": "caption"},
            },
            "translate": {
                "description": (
                    "Machine-translate a typed subtitle document into target_language and write it as SRT or "
                    "WebVTT (docs/adr/ADR-004-translation-engine.md). Cue ids and start/end times are kept "
                    "exactly; each cue is translated as one segment and re-broken for the target language "
                    "(locale profiles for ja/zh/ko, word wrap otherwise), then validated with the target "
                    "language's line-length and reading-speed limits (observations, never rewrites). "
                    "engine: 'argos' (default, offline Argos Translate; the language package must already be "
                    "installed, nothing is downloaded), 'deepl' (DeepL API, key only from the DEEPL_AUTH_KEY "
                    "environment variable) or 'memory' (exact source->target pairs in translation_memory; "
                    "deterministic, no model). glossary terms are sent as a DeepL glossary or substituted "
                    "into the source for Argos, and checked in every output (GLOSSARY_TERM_MISSING). The "
                    "response carries the translated document as `subtitle`, ready for mux or render."
                ),
                "inputs": [
                    "subtitle",
                    "target_language",
                    "format",
                    "output_path",
                    "engine?",
                    "glossary?",
                    "translation_memory?",
                    "constraints?",
                ],
                "outputs": [
                    "output",
                    "sha256",
                    "size",
                    "timeline",
                    "observation",
                    "subtitle",
                    "engine",
                    "engine_version",
                    "source_language",
                    "target_language",
                ],
                "formats": list(SUPPORTED_FORMATS),
                "engines": list(ENGINES),
                "default_engine": DEFAULT_ENGINE,
                "deterministic_engines": ["memory"],
            },
            "mux": {
                "description": (
                    "Mux several subtitle documents (one per language) into one video as language-tagged, "
                    "player-toggleable subtitle tracks, in one ffmpeg-skill `caption --mode mux` call "
                    "(`--srt FILE:LANG` per track; ffmpeg-skill 1.16+ for more than one track). Video and "
                    "audio are copied untouched. The codec follows the output container: mov_text for "
                    ".mp4/.m4v/.mov (whose language tag ffmpeg-skill writes as ISO 639-2, e.g. jpn), SubRip "
                    "for .mkv, WebVTT for .webm. Each track's language is its document's `language`; two "
                    "tracks may not share one. `default_language` marks one track default (an MPEG-4 file "
                    "always enables its first track). Every document is validated against the probed video "
                    "duration; timing is passed through unchanged."
                ),
                "inputs": [
                    "video_input",
                    "tracks",
                    "output_path",
                    "default_language?",
                    "audio_stream?",
                    "constraints?",
                    "format?",
                ],
                "outputs": ["output", "sha256", "size", "timeline", "observation", "engine", "mode", "tracks"],
                "formats": list(RENDER_SUPPORTED_FORMATS),
                "containers": sorted(MUX_CONTAINERS),
                "delegates_to": {"skill_id": "ffmpeg-skill", "tool": "caption"},
            },
        },
        "provides": [
            {"id": PROVIDES[op], "lifecycle": "EXPERIMENTAL", "tool_id": f"{TOOL_ID_PREFIX}/{op}"}
            for op in OPERATIONS
        ],
        "dependencies": dependencies(),
        "capabilities": {
            "formats": list(SUPPORTED_FORMATS),
            "render_formats": list(RENDER_SUPPORTED_FORMATS),
            "render_modes": sorted(ALLOWED_RENDER_MODES),
            "translation_engines": list(ENGINES),
            "mux_containers": sorted(MUX_CONTAINERS),
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
            "render": ["mode", "audio_stream"],
            "translate": ["target_language", "engine", "glossary", "translation_memory"],
            "mux": ["tracks", "default_language", "audio_stream"],
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
