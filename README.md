# subtitle-skill

A deterministic **subtitle production/rendering execution skill**.

`subtitle-skill` takes a typed subtitle request, validates it, generates a
subtitle file (SRT/WebVTT) and — for burn-in rendering — delegates the
actual media processing to `ffmpeg-skill`. It performs no AI judgment,
no editorial decision-making, and never builds an arbitrary FFmpeg
command.

## 1. Architecture / where this skill sits

```
video-production-agent
  Observation → Event → Context → Inference
  → Policy / Preference / Constraint → Decision
  → ProductionPlan → Project IR → Compiler
  → subtitle-skill                              <-- this repo
       → typed request
       → subtitle validation
       → subtitle timeline validation
       → subtitle format conversion
       → subtitle rendering / output
       → deterministic execution
       → output validation
       → structured response
       → provenance
       → (delegates media processing) → ffmpeg-skill
```

`subtitle-skill` is a *leaf* execution skill. It receives a fully-decided,
typed request and executes it mechanically. It makes **no** decisions
about what subtitles should say, how they should be split, whether a
speaker label should be shown, or when to burn them in — those are
`video-production-agent` responsibilities, expressed as typed request
parameters.

## 2. Responsibility boundary

| Concern | Owner |
|---|---|
| Speech recognition / transcription | `transcription-skill` |
| Speaker diarization | `transcription-skill` (or a future dedicated skill) |
| Deciding *what* the subtitle text should say, how to split/merge cues, which speaker to show | `video-production-agent` (Inference/Decision) |
| Subtitle cue/timeline validation, SRT/WebVTT generation, burn-in orchestration | **`subtitle-skill`** |
| Building the actual FFmpeg command/filtergraph and encoding | `ffmpeg-skill` |

`subtitle-skill` treats a `transcription-skill` segment and a subtitle
`SubtitleCue` as **different types**. A caller (typically
`video-production-agent`) is responsible for the `TranscriptSegment →
SubtitleCue` decision (splitting long sentences, whether to show a
speaker label, etc.) and must pass the result in as already-typed
`SubtitleCue` objects. `subtitle-skill` never performs that judgment
itself.

Explicitly out of scope for this repository (see STEP 23 of the original
task spec): AI/LLM logic, automatic transcription, diarization, semantic
subtitle generation, summarization, translation, automatic chaptering/
scene detection, automatic subtitle rewriting, conference-specific rules,
cloud upload, MCP/plugin loaders, arbitrary FFmpeg/shell/executable
execution.

## 3. Data model (`subtitle_skill.models`)

- **`SubtitleDocument`**: `id`, `version`, `language` (BCP-47-ish tag),
  `cues` (ordered tuple of `SubtitleCue`), `metadata`.
- **`SubtitleCue`**: `id`, `start` (sec), `end` (sec), `text`,
  `speaker` (optional), `style` (optional, allowlisted), `metadata`
  (optional, never rendered into subtitle text).
- **`SubtitleStyle`**: allowlisted fields only —
  `align`, `position`, `line`, `size`, `bold`, `italic`, `color`. There is
  no free-form style string; unknown fields are rejected.

Everything is parsed through `from_dict`/typed dataclasses — the rest of
the codebase never touches a raw subtitle dict.

## 4. Timeline & text validation (`subtitle_skill.validation`)

Fatal (rejected before any file is written), regardless of caller
parameters:
- `start < 0`, `end <= start`, `NaN`/`Infinity` timestamps
- duplicate cue ids
- empty/whitespace-only text, invalid control characters, invalid Unicode
- a cue extending past a supplied `video_duration`

Reported as `observation` issues but **never auto-corrected** (no cue is
ever deleted, reordered, merged, or shifted by this skill):
- `CUE_OVERLAP`
- `TOO_MANY_LINES` / `LINE_TOO_LONG` / `CUE_TOO_SHORT` / `CUE_TOO_LONG` /
  `READING_SPEED_TOO_HIGH`

Thresholds (`max_chars_per_line`, `max_lines`, `min_duration`,
`max_duration`, `reading_speed_cps`) are caller-supplied
`constraints`, not hard-coded domain assumptions. Defaults exist only so a
caller may omit `constraints` entirely; they are not asserted as
universally "correct" values.

## 5. Subtitle formats (`subtitle_skill.formats`)

Implemented: **SRT**, **WebVTT**. (ASS/SSA is a possible future addition,
not implemented here.)

Neither generator silently drops information it cannot represent:
- SRT has no native cue positioning — a cue with `style.align`,
  `position`, `line`, `size`, or `color` set raises `UNSUPPORTED_FORMAT`.
  `bold`/`italic` are rendered as `<b>`/`<i>` tags (widely supported by
  SRT players).
- WebVTT supports `align`/`position`/`line`/`size` as native cue settings.
  `color` has no safe, non-CSS-injecting representation in this
  implementation and also raises `UNSUPPORTED_FORMAT`.
- `speaker` is rendered as a plain-text `"Speaker: "` prefix on the first
  line in both formats (a fixed, typed convention — not a caller-supplied
  template).
- Cue/document `metadata` is provenance/auxiliary data and is
  intentionally **never** written into the rendered subtitle body of any
  format (there is no lossless way to embed arbitrary metadata in
  SRT/WebVTT text).

## 6. Contract (`subtitle-skill contract --json`)

```json
{
  "skill_id": "subtitle-skill",
  "version": "0.1.0",
  "contract_version": "1.0.0",
  "deterministic": true,
  "operations": {"generate": {...}, "render": {...}},
  "capabilities": {"formats": ["srt", "vtt"], "path_policy": true, "provenance": true, "reuse": true},
  "parameters": {"constraints": [...], "style": [...]},
  "errors": {"codes": [...], "non_retryable": [...]},
  "out_of_scope": [...]
}
```

This shape is subtitle-skill's own — no `run - --json` single-endpoint
convention exists in ffmpeg-skill (see §14): its CLI is one script per
tool, and this repository does not (yet) share a CLI convention document
with `media-analysis-skill`, `transcription-skill` or
`video-editing-skill`, whose sources were not inspected in this pass.
`generate` accepts both formats; `render` accepts only `srt` (§14).

## 7. Operations

### `generate`
Validates a subtitle document and writes an SRT/WebVTT file. No video I/O.

### `render`
Validates a subtitle document, generates the SRT file, then delegates
burn-in to ffmpeg-skill's `caption` tool (see §14). Requires
`video_input`. **`format` must be `"srt"`** — ffmpeg-skill's `caption`
tool has no WebVTT support, so `render` with `format: "vtt"` is rejected
with `UNSUPPORTED_FORMAT` before anything is generated.

Both operations share one request shape:

```json
{
  "operation": "generate | render",
  "workspace": "/absolute/path/to/workspace",
  "format": "srt | vtt",   // render: srt only (see above)
  "output_path": "relative/output.srt",
  "video_input": "relative/input.mp4",      // render only
  "video_duration": 123.4,                    // optional, for timeline validation
  "subtitle": {
    "id": "doc-1", "language": "ja",
    "cues": [{"id": "c1", "start": 0.0, "end": 2.0, "text": "...", "speaker": "A", "style": {"align": "center"}}]
  },
  "constraints": {"max_chars_per_line": 42, "max_lines": 2, "min_duration": 0.5, "max_duration": 10, "reading_speed_cps": 20}
}
```

`convert`, `validate`, `offset`, `merge` are documented as possible future
operations but are **not** implemented or advertised in the contract —
per STEP 10, an operation must not appear as supported unless it is
actually executable.

## 8. Security boundary

Before any parsing, every request (recursively, at any nesting depth) is
scanned for forbidden keys and rejected with `INVALID_REQUEST` if any is
present: `command`, `argv`, `shell`, `executable`, `filter`,
`filter_complex`, `vf`, `af`, `env`, `api_key`.

`subtitle-skill` never:
- runs a shell (`shell=True` is never used; every subprocess call is an
  argv list),
- accepts a caller-supplied executable path, FFmpeg filter string, or raw
  command,
- passes caller data into an environment variable used by a subprocess.

## 9. PathPolicy (`subtitle_skill.pathpolicy`)

Every input/output path is resolved relative to a single `workspace`
root:
- absolute paths and drive-qualified paths are rejected,
- `..` components are rejected via string inspection *before* any
  filesystem call,
- the fully resolved (symlink-following) path must still be inside the
  canonicalized workspace root — this catches a symlink planted inside
  the workspace pointing outside it,
- Windows reserved device names (`CON`, `PRN`, `COM1`, ...) are rejected.

This is defense in depth: `video-production-agent` is expected to run its
own PathPolicy too, but `subtitle-skill` never trusts a caller's path
hygiene alone.

## 10. Deterministic execution & reuse

An "identity" hash is computed from: skill version, contract version,
operation, the full canonical subtitle document, format, constraints,
and (for `render`) the input video's sha256 **and the resolved
ffmpeg-skill's own version** (read from its `package.json`, if present) —
a different ffmpeg-skill build can change encoder defaults/output bytes
for the same document and video, so an ffmpeg-skill upgrade must not
serve a stale cached render as if nothing changed. Timestamps, PIDs, and
temp paths are **never** part of this identity.

On each call, if an output file *and* a matching sidecar
(`<output>.subtitle-skill.json`) already exist with the same identity,
the sidecar's recorded sha256 is **re-verified against the file on disk**
before being reported as `"reused": true`. A corrupted or hash-mismatched
output is never returned as reused — it is regenerated.

## 11. Response / provenance

Success response (`status: "ok"`) includes: `skill`, `skill_version`,
`contract_version`, `operation`, `output`, `sha256`, `size`, `reused`,
`observation` (validation issues, possibly non-empty even on success),
`timeline` (cue count), `engine` / `engine_version` (render only —
ffmpeg-skill's own `package.json` version, or `null` if it has none),
`duration_ms`. The sidecar additionally records `engine_response`:
ffmpeg-skill's own reported `commands` (the ffmpeg command line it ran)
and `probe` (its own ffprobe of the output) for audit.

A response is only accepted as successful if the output file actually
exists and is non-empty — process exit code `0` alone is never treated
as success (see `operations._finish`).

## 12. Error model

Typed, stable codes (see `subtitle_skill.errors.ERROR_CODES`):
`INVALID_REQUEST`, `INVALID_INPUT`, `UNSUPPORTED_OPERATION`,
`UNSUPPORTED_FORMAT`, `INVALID_TIME_RANGE`, `DEPENDENCY_ERROR`,
`PATH_NOT_ALLOWED`, `MISSING_INPUT`, `OUTPUT_ERROR`, `VALIDATION_ERROR`,
`TOOL_ERROR`, `CANCELLED`, `INTERNAL_ERROR`. Each carries a `retryable`
boolean (see `NON_RETRYABLE_CODES`) — e.g. a bad request is never
retryable, a transient `DEPENDENCY_ERROR` talking to `ffmpeg-skill` is.

## 13. `doctor --json`

Reports only operations that can actually run right now: `generate` is
always listed; `render` is listed **only** when an ffmpeg-skill install is
located *and* ffmpeg-skill's own `doctor` confirms the `caption` tool's
required capabilities (`ffmpeg`, `ffprobe`, `encoder:libx264`,
`encoder:aac`, `filter:subtitles`) are available — subtitle-skill asks
ffmpeg-skill's own detection rather than re-implementing FFmpeg capability
probing. `render_supported_formats` is always `["srt"]`. If `render` is
unavailable, `problems` contains a `DEPENDENCY_ERROR` explaining why
(install not found vs. a specific missing capability), and `healthy` is
`false`; an *unknown* (not proven missing) capability is reported as a
`"severity": "warning"` problem without disabling `render`, mirroring
ffmpeg-skill's own three-state (`available`/`missing`/`unknown`) doctor
semantics.

## 14. `ffmpeg-skill` integration

This section was **verified against ffmpeg-skill's actual source**
(`kajisho5/ffmpeg-skill`, commit `2abd89ce4cda31b70fb44dcf3ef225cdec92aada`,
skill version `0.9.1`, `contract_version "1.0"`) — `docs/contract.md`,
`scripts/_contract.py`, `scripts/caption.py` and `scripts/_common.py` —
and by actually running `scripts/caption.py` and `scripts/probe.py`
against a real video, not by reading its README alone.

**There is no single `run - --json` dispatch endpoint in ffmpeg-skill.**
Its `bin/install.js` CLI only handles `contract`/`doctor`/install; every
other tool is its own script, run directly:

```
python3 <ffmpeg-skill-install-dir>/scripts/<tool>.py [args] --json [--dry-run]
```

`render` delegates to ffmpeg-skill's `caption` tool
(`scripts/caption.py`), the only ffmpeg-skill tool that burns subtitles
into a video. `subtitle_skill.engine`:

1. locates the ffmpeg-skill install directory (the one that directly
   contains `scripts/caption.py`) via `SUBTITLE_SKILL_FFMPEG_SKILL_DIR`,
   or by checking the directories ffmpeg-skill's own installer writes to:
   `~/.claude/skills/ffmpeg-skill`, `~/.cursor/skills/ffmpeg-skill`,
   `~/.codex/skills/ffmpeg-skill`, `./.claude/skills/ffmpeg-skill`;
2. runs `scripts/probe.py <video> --json` on the input first (ffmpeg-skill's
   own "probe first" convention) to confirm it has a video stream and to
   record its duration;
3. runs
   `scripts/caption.py <video> --srt <srt> -o <output> --json`
   — a fixed argv list, `sys.executable` as the interpreter (ffmpeg-skill's
   scripts are Python-standard-library-only, so any compliant interpreter
   works), never a shell;
4. accepts the result only when **all** of: exit code `0`, stdout parses
   as JSON with `"status": "completed"`, the output file exists and is
   non-empty, the response's own `probe.video` is present, and the
   output's duration is within 0.25s of the input's (caption burn-in
   re-encodes but must not change the length) — exit code `0` alone is
   never sufficient (see `engine._run_tool` / `engine.burn_in`).

Failure responses follow ffmpeg-skill's own shape,
`{"status": "failed", "error": {"kind": "input"|"ffmpeg"|"missing_tool", "message": "..."}}`,
mapped onto subtitle-skill's error codes: `input` → `INVALID_INPUT`
(e.g. the video has no video stream), `missing_tool` → `DEPENDENCY_ERROR`
(ffmpeg/ffprobe absent), `ffmpeg` → `TOOL_ERROR` (the encode itself
failed). A response ffmpeg-skill did not produce at all (crash, timeout,
malformed stdout) is `DEPENDENCY_ERROR`.

**`render` only accepts `format: "srt"`.** ffmpeg-skill's `caption.py`
burns SRT or ASS files (`--srt` / `--ass`); it has no WebVTT support at
all. Requesting `render` with `format: "vtt"` is rejected up front with
`UNSUPPORTED_FORMAT`, both in `operations._run_render` and in
`engine.burn_in` (`doctor --json`'s `render_supported_formats` reflects
this too). `generate` is unaffected and still supports both formats,
since it never touches ffmpeg-skill.

**What subtitle-skill does *not* verify:** ffmpeg-skill's own contract
marks `caption` as `requires_visual_verification: true` and recommends
running `ffmpeg-skill/look` (a PNG contact sheet) on the output afterwards
for a human or agent to inspect. subtitle-skill deliberately does not run
or interpret `look` itself — judging whether the burnt-in captions
*look* right is exactly the kind of visual/AI judgement this skill's
mandate excludes (see §2); that step belongs to whoever is driving the
render (typically `video-production-agent`). subtitle-skill's own
verification is the deterministic, machine-checkable part: output exists,
has a video stream, and its duration matches the input.

## 15. Ecosystem

```
video-production-agent
        │
        ├── media-analysis-skill      (not integrated with; source not reviewed)
        ├── transcription-skill       (upstream of this skill's input; §2)
        ├── subtitle-skill            <-- this repo
        ├── audio-production-skill    (not integrated with; source not reviewed)
        ├── motion-graphics-skill     (not integrated with; source not reviewed)
        ├── color-grading-skill       (not integrated with; source not reviewed)
        ├── thumbnail-skill           (not integrated with; source not reviewed)
        └── ffmpeg-skill              (downstream of `render`; §14, verified integration)
```

Only the `ffmpeg-skill` edge is an actual, verified integration (§14).
Every other skill above is named for orientation only — this repository
does not call them, and their contracts were not read in this pass; do
not read "listed" as "integrated".

## 16. Testing

```
pip install -e .
pip install pytest
pytest -q
```

Test files:
- `test_models.py` — typed model validation (timeline, uniqueness, style allowlist)
- `test_validation.py` — timeline/text validation, overlap reporting, configurable constraints
- `test_formats.py` — SRT/WebVTT generation, Unicode, unsupported-style rejection, ordering
- `test_pathpolicy.py` — traversal, absolute paths, symlink escape, reserved names
- `test_security.py` — forbidden-key rejection (top-level and nested), path rejection
- `test_cli.py` — `contract`/`doctor`/`run` JSON process boundary, malformed input, typed errors, determinism/reuse
- `test_doctor.py` — render availability tied to ffmpeg-skill's real, dynamically detected capabilities
- `test_engine_render.py` — render delegation against a **vendored, real copy** of ffmpeg-skill's
  `caption`/`probe` scripts (see `tests/fixtures/ffmpeg_skill_vendor/README.md`) — not a hand-written
  stub — including format rejection, missing-install, no-video-stream, reuse, and (when a real `ffmpeg`
  is present) an actual burn-in verified by `ffprobe` and by asserting ffmpeg-skill's own reported
  command line used the `subtitles=` filter against our generated SRT.

## 17. Cross-platform notes

`PathPolicy` rejects Windows drive letters/backslash-absolute paths and
reserved device names regardless of host OS, so behavior is consistent on
Linux/macOS/Windows. `pathlib.Path` is used throughout instead of manual
string path building.
