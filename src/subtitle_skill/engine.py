"""Delegation to ffmpeg-skill, the execution skill that owns actual media processing.

subtitle-skill NEVER builds an FFmpeg command line, filter graph, or shell
string itself. Burn-in rendering is delegated to ffmpeg-skill's own
`caption` tool, invoked exactly as ffmpeg-skill's own contract and
`SKILL.md` document: a plain Python script under a `scripts/` directory,
run with a fixed argv list (no shell=True, no user-controlled executable,
argv, filter or env).

This module's shape was verified against kajisho5/ffmpeg-skill (commit
336e0c4d6311d2407daaa529ad71fee641f59b37, contract_version "1.0", skill
version 0.12.2) -- specifically `docs/contract.md`, `scripts/_contract.py`,
`scripts/caption.py` and `scripts/_common.py` -- and confirmed by actually
running `scripts/caption.py` and `scripts/probe.py` against a real video,
not by assumption. See README "ffmpeg-skill integration" for the
citations. (Previously verified against commit 2abd89c / skill version
0.9.1; re-verified after re-vendoring for kajisho5/subtitle-skill#3 --
`caption.py` gained `--mode mux` and `--audio-stream` in the interim,
both now wired through `burn_in()` below.)

Key facts this module depends on:
- there is no "ffmpeg-skill run" or single dispatch endpoint; each tool is
  its own script (`scripts/<tool>.py`), invoked as
  `python3 <ffmpeg-skill-dir>/scripts/<tool>.py [args] --json`.
- `caption.py` burns SRT or ASS files, never WebVTT. subtitle-skill's
  `render` operation therefore only supports format="srt". It can burn
  the SRT into the picture (`--mode burn`, the default) or mux it in as a
  soft, toggleable subtitle stream (`--mode mux`) untouched otherwise;
  both take only SRT (`--mode mux --ass` is refused by caption.py itself).
- success prints `{"status": "completed", "output", "dry_run", "commands",
  "probe": {...}}` on stdout with exit 0; failure prints
  `{"status": "failed", "error": {"kind": "input"|"ffmpeg"|"missing_tool",
  "message": "..."}}` on stdout (only when --json is passed) with a
  non-zero exit code (127 specifically when ffmpeg/ffprobe is missing).
- exit code 0 is necessary but not sufficient: this module additionally
  requires `status == "completed"`, a written, non-empty output file, and
  (for caption) a `probe.video` on the output with a duration consistent
  with the input's -- burning in captions must not change the length.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .errors import SubtitleSkillError
from .provenance import sha256_file

#: Explicit override for the ffmpeg-skill install directory (the directory
#: that directly contains `scripts/caption.py`). Never taken from a request
#: payload -- environment configuration only.
FFMPEG_SKILL_DIR_ENV = "SUBTITLE_SKILL_FFMPEG_SKILL_DIR"

#: Duration tolerance (seconds) between the source video and the captioned
#: output. caption.py re-encodes but does not cut or speed-change, so any
#: difference beyond typical encoder rounding indicates something is wrong.
_DURATION_TOLERANCE_SECONDS = 0.25

#: Maps ffmpeg-skill's own `error.kind` values (docs/contract.md
#: "JSON output") to subtitle-skill's typed error codes.
_ERROR_KIND_TO_CODE = {
    "input": "INVALID_INPUT",
    "missing_tool": "DEPENDENCY_ERROR",
    "ffmpeg": "TOOL_ERROR",
}

#: `caption.py --mode {burn,mux}` (argparse `choices`, ffmpeg-skill 0.11.0+).
#: An invalid mode is rejected here, before ever invoking caption.py: argparse
#: choices errors go to stderr with exit code 2 and print no JSON at all, so
#: letting a bad value reach caption.py would surface as an opaque
#: DEPENDENCY_ERROR ("did not print a JSON document") instead of a clear,
#: caller-actionable INVALID_INPUT.
ALLOWED_RENDER_MODES = frozenset({"burn", "mux"})


def _candidate_install_roots() -> list[Path]:
    """Directories ffmpeg-skill's own installer (bin/install.js) writes to.

    Verified against ffmpeg-skill 0.9.1's `bin/install.js` target table
    (re-checked at 0.12.2 -- the target table is unchanged):
    `~/.claude/skills`, `~/.cursor/skills`, `~/.codex/skills` (per-agent
    global installs) and `./.claude/skills` (its `--project` mode).
    """
    home = Path.home()
    return [
        home / ".claude" / "skills" / "ffmpeg-skill",
        home / ".cursor" / "skills" / "ffmpeg-skill",
        home / ".codex" / "skills" / "ffmpeg-skill",
        Path.cwd() / ".claude" / "skills" / "ffmpeg-skill",
    ]


def _looks_like_ffmpeg_skill(root: Path) -> bool:
    return (root / "scripts" / "caption.py").is_file() and (root / "scripts" / "_common.py").is_file()


def resolve_ffmpeg_skill_root() -> Optional[Path]:
    """Locate the ffmpeg-skill install directory, or None if not found.

    `SUBTITLE_SKILL_FFMPEG_SKILL_DIR` takes precedence for explicit
    configuration (tests, non-standard installs); otherwise the known
    install locations are searched.
    """
    configured = os.environ.get(FFMPEG_SKILL_DIR_ENV)
    candidates = [Path(configured)] if configured else _candidate_install_roots()
    for root in candidates:
        if root.is_dir() and _looks_like_ffmpeg_skill(root):
            return root
    return None


def is_available() -> bool:
    return resolve_ffmpeg_skill_root() is not None


#: Returned by `ffmpeg_skill_version` when ffmpeg-skill's package.json is
#: missing or unreadable. A known consumer (video-production-agent's
#: SubtitleAdapter, verified against its actual source) requires a
#: render response's `engine_version` to be a non-empty string -- it
#: treats anything else (including a JSON `null`) as an invalid result,
#: not a render failure worth retrying. Returning this constant instead
#: of `None` keeps the field truthful (it names exactly what happened --
#: not a fabricated version number) while never breaking that contract.
UNKNOWN_ENGINE_VERSION = "unknown"


def ffmpeg_skill_version(root: Path) -> str:
    """Read the installed ffmpeg-skill's own version from its package.json,
    for human-readable provenance only. ffmpeg-skill's installer
    (bin/install.js) copies package.json alongside scripts/ into every
    install target; its absence (e.g. a hand-built scripts/ directory, or
    this repo's own test fixture) is not an error -- provenance records
    `UNKNOWN_ENGINE_VERSION` rather than a missing/null field.

    This is deliberately NOT used as the determinism/cache anchor: a
    self-reported version string is only as trustworthy as whoever last
    edited package.json. A hand-patched `scripts/caption.py` with a stale
    package.json would silently keep reporting the old version. See
    `ffmpeg_skill_script_hash` for the content-addressed anchor actually
    used in identity/reuse.
    """
    manifest = root / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return str(data["version"])
    except (OSError, ValueError, KeyError):
        return UNKNOWN_ENGINE_VERSION


def ffmpeg_skill_script_hash(root: Path) -> str:
    """sha256 of the exact `caption.py` and `_common.py` bytes that will
    actually be executed (`_common` is imported by `caption.py` and holds
    the shared ffmpeg invocation logic, so a change there changes behavior
    too even if `caption.py` itself is untouched).

    This -- not `ffmpeg_skill_version`'s self-reported package.json string
    -- is what subtitle-skill uses as the render determinism/cache anchor:
    it changes if and only if the code that will actually run changes,
    regardless of whether whoever changed it also remembered to bump
    package.json.
    """
    parts = [
        sha256_file(root / "scripts" / "caption.py"),
        sha256_file(root / "scripts" / "_common.py"),
    ]
    return hashlib.sha256("".join(parts).encode("ascii")).hexdigest()


def _run_tool(root: Path, tool: str, argv: list[str], *, timeout_seconds: int) -> dict:
    """Run one ffmpeg-skill tool script and return its parsed JSON document.

    Always appends `--json` ourselves: ffmpeg-skill's own transports treat
    `probe`/`look` as JSON-by-default and skip appending it, but a failure
    from those two tools without `--json` prints a plain-text error with no
    JSON envelope at all (confirmed by running `probe.py` on a missing file
    with and without `--json`) -- so passing it explicitly, for every tool,
    is what actually gets a machine-readable failure document every time.
    """
    script = root / "scripts" / f"{tool}.py"
    cmd = [sys.executable, str(script), *argv, "--json"]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR", f"ffmpeg-skill/{tool} timed out after {timeout_seconds}s"
        ) from exc
    except OSError as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"failed to launch ffmpeg-skill/{tool}: {exc}") from exc

    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill/{tool} did not print a JSON document (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:2000]}",
        ) from exc

    if not isinstance(response, dict) or "status" not in response:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR", f"ffmpeg-skill/{tool} returned an unrecognised document: {response!r}"
        )

    if response["status"] == "failed":
        error = response.get("error") or {}
        kind = error.get("kind")
        message = error.get("message", "unknown error")
        code = _ERROR_KIND_TO_CODE.get(kind, "TOOL_ERROR")
        raise SubtitleSkillError(code, f"ffmpeg-skill/{tool} failed ({kind}): {message}")

    if response["status"] != "completed":
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR", f"ffmpeg-skill/{tool} returned an unexpected status: {response['status']!r}"
        )

    if proc.returncode != 0:
        # The contract states exit 0 iff status == "completed"; a mismatch
        # means the execution skill itself is misbehaving, not a normal
        # input/ffmpeg failure (those already raised above).
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill/{tool} reported status=completed but exited {proc.returncode}",
        )

    return response


def probe(root: Path, media_path: Path, *, timeout_seconds: int = 120) -> dict:
    """Run ffmpeg-skill's own `probe` tool. Its success document has no
    `status`/`output` envelope (see docs/contract.md output_schema for
    "probe") -- it *is* the measurement document -- so this does not go
    through `_run_tool`'s envelope checks, only its failure handling.
    """
    script = root / "scripts" / "probe.py"
    cmd = [sys.executable, str(script), str(media_path), "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"ffmpeg-skill/probe timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"failed to launch ffmpeg-skill/probe: {exc}") from exc

    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill/probe did not print a JSON document (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:2000]}",
        ) from exc

    if isinstance(doc, dict) and doc.get("status") == "failed":
        error = doc.get("error") or {}
        kind = error.get("kind")
        message = error.get("message", "unknown error")
        code = _ERROR_KIND_TO_CODE.get(kind, "TOOL_ERROR")
        raise SubtitleSkillError(code, f"ffmpeg-skill/probe failed ({kind}): {message}")

    if proc.returncode != 0 or not isinstance(doc, dict) or "duration" not in doc:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR", f"ffmpeg-skill/probe returned an unrecognised document: {doc!r}"
        )
    return doc


def burn_in(
    *,
    video_path: Path,
    subtitle_path: Path,
    subtitle_format: str,
    output_path: Path,
    mode: str = "burn",
    audio_stream: Optional[int] = None,
    language: Optional[str] = None,
    timeout_seconds: int = 600,
) -> dict:
    """Delegate subtitle burn-in (or soft-mux) to ffmpeg-skill's `caption` tool.

    Only `srt` is accepted: ffmpeg-skill's `caption.py` burns SRT or ASS
    files, never WebVTT (`--srt` / `--ass`; there is no `--vtt`), confirmed
    from its argparse parser, not assumed. This holds for both `mode`
    values -- `--mode mux` additionally refuses `--ass` on caption.py's own
    side (no soft-subtitle equivalent for ASS styling), but subtitle-skill
    never sends `--ass` in the first place, so that path is unreachable here.

    `mode="mux"` copies the input's video and audio streams untouched and
    adds the SRT as a separate, player-toggleable subtitle stream instead of
    rendering it into the picture -- `caption.py --mode mux` (ffmpeg-skill
    0.11.0+). `audio_stream`, when given, selects which 0-based audio track
    of a multi-track input (dubbed languages, M&E stems) caption.py keeps
    (`--audio-stream N`; ffmpeg-skill 0.12.0+, closing ffmpeg-skill#55) --
    caption.py itself validates it against the input's actual track count
    and rejects an out-of-range value as `kind: "input"`. `language`, when
    given with `mode="mux"`, is forwarded as `--language` and tagged onto
    the newly added subtitle stream's metadata (`-metadata:s:s:N
    language=...`) -- caption.py's own `--language` also feeds `--transcribe`,
    which subtitle-skill never uses, so this is mux-only here.

    Not enforced or normalized here: `language` is forwarded exactly as the
    document states it (subtitle-skill makes no editorial decisions), but a
    real MOV/MP4 muxer quirk confirmed directly against ffmpeg -- not
    assumed -- means it may not actually end up in the output's stream tags
    for every container. caption.py picks the mux subtitle codec (and thus
    which muxer receives the metadata) from the output extension: `.mkv`
    (`srt`) and `.webm` (`webvtt`) write whatever string `--language` is
    given verbatim, but `.mp4`/`.m4v`/`.mov` (`mov_text`) silently drop a
    `-metadata:s:s:N language=...` value that is not a 3-letter ISO 639-2
    code -- a plain 2-letter BCP47 tag like "ja"/"en" (exactly what
    `SubtitleDocument.language`'s own `_is_bcp47_ish` accepts, and what a
    real caller is likely to send) is silently lost from an .mp4/.mov
    output with no error, warning, or non-zero exit anywhere in the chain.
    """
    if subtitle_format != "srt":
        raise SubtitleSkillError(
            "UNSUPPORTED_FORMAT",
            f"render only supports format='srt' (ffmpeg-skill/caption burns SRT or ASS, never {subtitle_format!r})",
        )

    if mode not in ALLOWED_RENDER_MODES:
        raise SubtitleSkillError(
            "INVALID_INPUT", f"mode must be one of {sorted(ALLOWED_RENDER_MODES)}, got {mode!r}"
        )

    if audio_stream is not None and (isinstance(audio_stream, bool) or not isinstance(audio_stream, int) or audio_stream < 0):
        raise SubtitleSkillError("INVALID_INPUT", f"audio_stream must be a non-negative integer, got {audio_stream!r}")

    root = resolve_ffmpeg_skill_root()
    if root is None:
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"ffmpeg-skill install not found (checked {FFMPEG_SKILL_DIR_ENV} and the standard "
            "~/.claude, ~/.cursor, ~/.codex and ./.claude skills directories)",
        )

    input_probe = probe(root, video_path, timeout_seconds=timeout_seconds)
    if not input_probe.get("video"):
        raise SubtitleSkillError("INVALID_INPUT", f"input has no video stream: {video_path}")
    input_duration = input_probe.get("duration")

    argv = [str(video_path), "--srt", str(subtitle_path), "-o", str(output_path)]
    if mode != "burn":
        argv += ["--mode", mode]
    if audio_stream is not None:
        argv += ["--audio-stream", str(audio_stream)]
    if mode == "mux" and language:
        argv += ["--language", language]

    response = _run_tool(
        root,
        "caption",
        argv,
        timeout_seconds=timeout_seconds,
    )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise SubtitleSkillError("OUTPUT_ERROR", "ffmpeg-skill/caption reported success but output is missing/empty")

    output_probe = response.get("probe")
    if not isinstance(output_probe, dict) or not output_probe.get("video"):
        raise SubtitleSkillError(
            "OUTPUT_ERROR", "ffmpeg-skill/caption reported success but the output has no video stream"
        )
    output_duration = output_probe.get("duration")
    if (
        isinstance(input_duration, (int, float))
        and isinstance(output_duration, (int, float))
        and abs(output_duration - input_duration) > _DURATION_TOLERANCE_SECONDS
    ):
        raise SubtitleSkillError(
            "OUTPUT_ERROR",
            f"output duration {output_duration}s differs from input duration {input_duration}s "
            f"by more than {_DURATION_TOLERANCE_SECONDS}s",
        )

    response = dict(response)
    response["engine_skill_version"] = ffmpeg_skill_version(root)
    response["engine_script_sha256"] = ffmpeg_skill_script_hash(root)
    return response
