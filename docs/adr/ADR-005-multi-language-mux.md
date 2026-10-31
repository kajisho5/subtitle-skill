# ADR-005: Multi-Language Mux Is One ffmpeg-skill `caption --mode mux` Call With Timing Left Alone

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 6 asks for several subtitle documents to go into one video ("Done when: 2-language integration test"). Step 5 (`translate`, [ADR-004](ADR-004-translation-engine.md)) produces the second language; this step delivers both.

Every media operation goes through ffmpeg-skill. This repository never builds an ffmpeg command line (`engine.py`'s module docstring). Read from ffmpeg-skill 2.24.0 (commit `86b5145c`) on 2026-10-31:

1. `caption.py --mode mux` takes `--srt` repeatedly, each with an optional `:LANG` suffix (`--srt en.srt:en --srt ja.srt:ja`), plus `--track-title` (repeated, in the same order) and `--default-track LANG`. These arrived in 1.16.0 (its CHANGELOG). Before that, `--mode mux` added one track tagged with `--language`.
2. The codec follows the output extension (`mux_subtitle_codec`): `mov_text` for `.mp4`/`.m4v`/`.mov`, `srt` (SubRip) for `.mkv`, `webvtt` for `.webm`. Anything else is refused.
3. MPEG-4 drops a two-letter language code without a word, so caption.py converts it to ISO 639-2 (`en` → `eng`, `ja` → `jpn`). Matroska keeps the code as given. MPEG-4 also drops `title` and always enables its first subtitle track. caption.py reports this in `tracks[]` and `notes`.
4. caption.py refuses two tracks with the same language and refuses `--mode mux --ass`.
5. caption.py runs its own layout (`lay_out`: wrap, split past `--max-lines`, lengthen to `--min-duration`, default 1.0 s) over the **first** `--srt` only, and muxes an adjusted copy when anything changed. The other tracks are muxed as written.

## Decision

1. **A new `mux` operation**, not a mode of `render`. `render` takes one `subtitle`; `mux` takes `tracks: [{subtitle, title?}]`, plus `default_language` and `audio_stream`. `render`'s `mode: "mux"` stays as it was, for one track.
2. **One caption.py call for all tracks.** Each document is validated against ffmpeg-skill's probed duration, written as SRT (`formats.generate_srt`), and passed as `--srt FILE:LANG` in request order. Chaining one call per language would re-mux the video once per language and needs nothing from the newer flags, but titles and the default track would then be decided per call.
3. **Timing passes through unchanged.** subtitle-skill's SRTs are already laid out and validated, so the call adds `--min-duration 0` and a `--max-lines` one above the longest cue. Without this, a 0.3 s cue in the first track only would come out 1 s long, and the tracks would disagree with each other. `tests/test_mux.py` extracts both tracks with ffmpeg and checks the timestamps.
   - The line breaks cannot be protected the same way. When a line of the first track is wider than caption.py's safe width for the frame, it re-wraps that track and muxes its adjusted copy (`<output>_adjusted.srt`, left next to the output by caption.py). The timing still passes through. subtitle-skill detects this (the muxed file caption.py reports for track 0 is not the SRT it passed) and reports a `TRACK_RELAID_OUT` warning for that track, stored in the sidecar so a reused result reports it too. At real frame sizes this needs a very long line. In the tests it happens on a 64-pixel frame.
4. **Languages come from each document's `language`**, primary subtag lower-cased to match caption.py's grammar (2–3 letters, then 2–8 character subtags). Duplicates and longer primaries are refused before any media work. The ISO 639-2 conversion for MPEG-4 is left to caption.py, the one place that knows the container. subtitle-skill reports what it wrote as `tracks[].stored_language`.
5. **Titles on every track or on none.** caption.py pairs titles with files by position. A title on the second track only cannot be passed without inventing one for the first.
6. **Version check by capability, not number.** More than one track, a title or a default track needs `--track-title`/`--default-track` in the installed `caption.py` (the same text check `--overwrite` already uses). An older install gets `DEPENDENCY_ERROR` naming 1.16, and a single untitled track still works there through `--srt FILE --language LANG`.
7. **Verified after the run:** the output exists, has a video stream and keeps the input's duration (as `render` checks), and caption.py reports exactly one new track per requested track.
8. **The test fixture is re-vendored to ffmpeg-skill 2.24.0.** The 0.12.2 copy has no multi-track mux. The whole existing suite passed against 2.24.0 before the copy was replaced (the daily drift check already ran it against main).

## Consequences

- `mux` writes `.mkv`/`.mp4`/`.m4v`/`.mov`/`.webm`. `.mkv` is the one container where every tag, title and "no default track" survives. The README and SKILL.md say so.
- **Known gaps, in ffmpeg-skill, not changed here:**
  - ASS cannot be muxed as a soft track: caption.py refuses `--mode mux --ass`. subtitle-skill generates no ASS either.
  - caption.py has no flag that turns its layout pass off for `--mode mux`. subtitle-skill neutralises its timing changes with `--min-duration 0` and a high `--max-lines`. Its re-wrap of the first track to the frame width (meaningless for a soft track, which the player lays out) can only be reported (`TRACK_RELAID_OUT`), not prevented. A `--no-layout` (or "skip layout in mux mode") in caption.py would close this.
  - An MPEG-4 file cannot leave every subtitle track disabled.
- `render`'s existing `mode: "mux"` does not pass `--min-duration 0`, so caption.py may still lengthen its short cues. It is left unchanged here, because it is a behaviour change to an operation video-production-agent already calls.
- `contract --json` gains the `mux` operation, `capabilities.mux_containers` and `parameters.mux`, together with `translate` (ADR-004 decision 8). video-production-agent must re-pin.
