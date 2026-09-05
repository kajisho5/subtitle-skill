---
name: subtitle-skill
description: Deterministic subtitle validation, SRT/WebVTT generation, and video caption burn-in. Given an already-decided, typed subtitle document (cue text, timing, optional speaker/style), validate its timeline, generate an SRT or WebVTT file, or burn it into a video by delegating to ffmpeg-skill's caption tool. Use this skill only after another agent has already decided what the subtitle cues should say and how they should be split -- it performs no transcription, no diarization, no cue-splitting judgement, and no "does this look right" visual check.
---

# subtitle-skill

This document is for an agent deciding whether and how to call
subtitle-skill. It is not a substitute for reading `README.md`
(architecture, data model, validation rules, security) or
`contract --json` (the machine-readable operation/format/error list) —
read both before integrating.

## When to use this skill

Use subtitle-skill when you already have subtitle **cues** — text with
start/end timestamps, decided by you or an upstream step — and need to:

- validate that timeline (catch overlaps, bad timestamps, unreadable
  cues) before shipping it,
- turn it into an SRT or WebVTT file,
- burn it into a video.

Do **not** use subtitle-skill to:

- transcribe audio (that's `transcription-skill`),
- decide how to split a long sentence into cues, whether to show a
  speaker label, or word subtitle text — that judgement is yours; hand
  subtitle-skill the finished `SubtitleCue`s, not a `TranscriptSegment`,
- translate, summarize, or rewrite subtitle text,
- decide *whether* a render "looks good" — subtitle-skill's render
  verification is limited to what a machine can check (file exists, has
  a video stream, duration preserved); it does not run or interpret
  ffmpeg-skill's `look` (visual contact sheet). If you need to confirm
  the captions are legible/well-placed, run `ffmpeg-skill/look` on the
  output yourself and inspect it — that is your judgement call, not
  subtitle-skill's.

**Never delegate the "did this render correctly" decision to
subtitle-skill.** A successful `render` response means: the request was
valid, ffmpeg-skill's `caption` tool exited with `status: "completed"`,
and the output file exists with a video stream and unchanged duration.
It does **not** mean the captions are positioned, sized, or timed the
way you wanted — that is a visual judgement outside this skill's scope.

## Building a SubtitleDocument

```json
{
  "id": "doc-1",
  "language": "en",
  "cues": [
    {"id": "c1", "start": 0.0, "end": 2.5, "text": "Hello and welcome"},
    {"id": "c2", "start": 2.5, "end": 5.0, "text": "to the show.", "speaker": "Host"}
  ]
}
```

- `start`/`end` are seconds, `start < end`, both finite and `>= 0`.
- `id` must be unique per document — subtitle-skill rejects duplicates.
- `speaker` is rendered as a plain `"Speaker: "` text prefix on the
  cue's first line — it is not a caption-styling decision, just a fixed
  convention (see README §5).
- `style` is an allowlisted, per-cue object (`align`, `position`,
  `line`, `size`, `bold`, `italic`, `color`) for `generate`'s SRT/WebVTT
  output. It is **not** forwarded to ffmpeg-skill's `caption` tool during
  `render` (ffmpeg-skill's caption styling is global-per-call, not
  per-cue, so there is no lossless translation — see README §14). If you
  need font/color/position control on a burned-in video, call
  ffmpeg-skill's `caption` tool yourself with those flags instead of
  going through subtitle-skill's `render`.

## Fatal vs. warning: read `observation`, don't assume success means "clean"

A successful response can still carry a non-empty `observation` array —
these are warnings, not failures, and subtitle-skill never auto-fixes
them (no cue is deleted, merged, or shifted):

| code | meaning |
|---|---|
| `CUE_OVERLAP` | two cues' time ranges overlap |
| `TOO_MANY_LINES` / `LINE_TOO_LONG` | exceeds `constraints.max_lines` / `max_chars_per_line` |
| `CUE_TOO_SHORT` / `CUE_TOO_LONG` | outside `constraints.min_duration` / `max_duration` |
| `READING_SPEED_TOO_HIGH` | more text than `constraints.reading_speed_cps` allows for the cue's duration |

Fatal problems (`VALIDATION_ERROR`, `INVALID_TIME_RANGE`, ...) raise
before any file is written — there is no partial output to clean up.

If you want subtitle-skill to enforce your own house style (e.g. max 32
chars/line), pass `constraints` in the request — do not treat the
built-in defaults as "the correct" values; they exist only so a caller
can omit `constraints` entirely.

## `generate` vs. `render`

- **`generate`**: no video I/O. Validates the document, writes an SRT or
  WebVTT file. Use this when you only need the subtitle file (e.g. to
  hand to another tool, or to review before committing to a burn-in).
- **`render`**: requires `video_input`; `format` must be `"srt"`.
  Validates, generates the SRT, then delegates the actual burn-in to
  ffmpeg-skill's `caption` tool. Requesting `render` with `format: "vtt"`
  fails immediately with `UNSUPPORTED_FORMAT` — do not retry with a
  different format expecting it to work; ffmpeg-skill has no WebVTT
  support to fall back to. `render` measures the real video duration
  itself before rendering and rejects a cue past it — you do not need to
  (and cannot rely on) passing a correct `video_duration` hint for this;
  it exists only as an optional, non-authoritative early check.

## ffmpeg-skill dependency

`render` only works when an ffmpeg-skill install with a working `caption`
tool is reachable. Call `doctor --json` first if you are not sure:
`"render"` only appears in `supported_operations` when ffmpeg-skill was
actually found and its required capabilities
(`ffmpeg`, `ffprobe`, `encoder:libx264`, `encoder:aac`, `filter:subtitles`)
are confirmed available. If `render` is missing from that list, do not
call it and expect it to work — read `problems` for why, and either fix
the environment (install ffmpeg-skill / ffmpeg) or fall back to
`generate` and hand the SRT to whatever can burn it in.

## Unsupported operation / format

Only `generate` and `render` are implemented; only those two appear in
`contract --json`'s `operations`. If you need `convert`, `validate`
(standalone), `offset`, or `merge`, they do not exist yet — do not guess
at a request shape for them; they return `UNSUPPORTED_OPERATION`.

## Failure handling

Every failure is a JSON envelope: `{"status": "error", "error": {"code",
"message", "retryable", "details"?}}`. `code` is one of the values in
`contract --json`'s `errors.codes`. Check `retryable` before deciding
whether to retry as-is — `VALIDATION_ERROR`/`INVALID_TIME_RANGE`/
`PATH_NOT_ALLOWED`/etc. will fail identically on retry (fix the request);
a `DEPENDENCY_ERROR` talking to ffmpeg-skill may be transient. Never
retry a failed request by loosening subtitle-skill's own validation —
if a cue is genuinely invalid (negative start, duplicate id, ...), that
is a bug in whatever built the `SubtitleDocument`, not something to paper
over here.
