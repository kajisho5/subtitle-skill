# ADR-006: Captions Move to the Top While a Face Covers the Bottom Caption Box

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 8: given detection-skill face boxes, each cue moves from the bottom to the top safe area while a face overlaps the bottom caption box. One move per cue, never mid-cue; a minimum hold so captions do not jump between neighbouring cues; a fixed region the caller names (a sign-language window, a lower third) is avoided the same way. Burn-in goes through ffmpeg-skill `caption --ass` with per-event alignment; a soft track gets `{\an8}`. Each move is recorded with the face box that caused it. OpenChatCut was read for the idea only; no code was taken.

Read on 2026-11-06:

1. detection-skill `docs/detections.md` (main `c46c91a`): `detections@1` has `frame_size {width, height}`, `sampling {method, every, times}`, and `frames [{t, detections: [{box: [x, y, w, h] source pixels, box_norm, score, label: null}]}]`. A face box is never an identity. `reframe@1` has per-sample `windows [{t, x, y, width, height}]` and one fixed window per shot in `shots [{start, end, x, y, width, height}]`, "for an engine without a time-varying crop (ffmpeg-skill today)".
2. ffmpeg-skill `caption.py` (the vendored 2.24.0 fixture and main): `--ass FILE` burns the file with its own styles (`ass=` filter, `fontsdir` of a font that covers the text); `--color`/`--size`/`--position` apply to the SRT path only, as one `force_style` for the whole burn. Its SRT defaults on a 288-line script: size 24, outline 2, vertical margin 30, alignment 2 (bottom centre). `--mode mux --ass` is refused.
3. qc-skill step 7 (`video.text_legible`, ADR-018, main `cc2a8ab`): WCAG contrast of a caller-named box at time `t` (default minimum 4.5) and ink height; no OCR.

## Decision

1. **`render` takes `placement`**, not a new operation: `faces` (workspace path, `detections@1`, kind `face`), `reframe` (optional, `reframe@1`), `avoid` (`[{name, box: [x, y, w, h] fractions, start?, end?}]`), `min_hold` (s, default 1.0), `max_lines` (caption box height, default 2), `min_score` (default 0). Without `placement` nothing changes: the same SRT and the same caption.py argv as before.
2. **Caption boxes** are fractions of the output frame derived from caption.py's own defaults: 5 % side margins, a 30/288 vertical margin, and `max_lines` x 1.25 x 24/288 of height (bottom: y 0.688-0.896; top: y 0.104-0.313 for two lines). The full width is used, since the drawn width of a line is not known before libass draws it; this is the conservative choice.
3. **One decision per cue.** The face samples taken while the cue is on screen (`start <= t < end`) count; a cue shorter than the sampling step that holds no sample uses the samples nearest its middle, within one step. A region counts while `[start, end)` overlaps the cue. A cue moves to the top when anything overlaps the bottom box and nothing overlaps the top box. When both are covered the cue stays at the bottom and the response carries a `CAPTION_OVERLAP_UNAVOIDABLE` observation; nothing is guessed.
4. **Minimum hold.** A run of bottom cues between two moved cues moves with them when the gap it fills (previous moved cue's end to next moved cue's start) is shorter than `min_hold`, unless the top box is covered for one of them. A run at the start or end of the document is left alone: there is no jump to avoid.
5. **Reframed output.** With `reframe`, each face box is mapped into the crop window that covers its sample time (the per-shot windows when present, else the per-sample ones), clipped, and normalised by the window size. A face cropped away is not an obstacle.
6. **Burn: subtitle-skill writes the ASS** (`formats/ass.py`): PlayResX/Y = the probed video size (rotation applied), caption.py's defaults scaled to that height, the document-wide `color`/`bold`/`size` `render` already reduces, `{\an8}` on moved cues, braces and backslashes in the text escaped. It is burned with `caption --ass`. **Soft track:** `mode: "mux"` writes `{\an8}` before the moved cues' SRT text.
7. **Provenance.** The response and sidecar carry `placement: {moves: [{cue_id, start, end, to, reason: face|region|min_hold, cause: {kind, t, box (source px), box_norm} | {kind: region, name, box_norm}}], unavoidable, inputs: {faces: {file, sha256, frames}, reframe?, regions}, top_cues}`. The faces/reframe sha256 and the top cue ids are part of the render identity, so a changed faces file is not reused.

## Done when, and how it was checked

- *A fixture with a face low in frame for 2-5 s moves only the cues in that span to the top*: `tests/fixtures/placement/faces_low_2_5s.json` (synthetic `detections@1`, 640x360, a face overlapping the bottom box at the samples 2.0-4.5 s and mid-frame otherwise). With ten 0.9 s cues one per second, exactly `c2`, `c3`, `c4` move (`tests/test_placement.py`). The burn test renders it through the vendored caption.py and reads the frames back: at 3.4 s only the upper half has text, at 6.4 s only the lower half. The soft-track test extracts the muxed track and counts three `{\an8}`.
- *qc-skill text legibility still passes on the burned frames*: checked locally, not in CI. qc-skill main (`cc2a8ab`, v1.0.0) was run unchanged (`qc run`, `kind: video`, `rules.video.text_regions` at each cue's middle, the caption box it was placed in, role `caption`, `min_contrast_ratio` 4.5) on the same fixture scaled to 1280x720: `video.text_legible` PASS, contrast 4.91-4.92 in both boxes, ink height 84-90 px at 1080. CI cannot run it: qc-skill is not a dependency of this repository, and adding one for a test would couple the two release trains.

## Consequences

- The face fixture is synthetic. No face detector ran here (detection-skill's YuNet weights were not downloaded); the code reads the documented `detections@1` shape and would read a real document the same way.
- A `placement` burn uses subtitle-skill's ASS instead of caption.py's SRT path, so caption.py's own SRT layout (`--max-lines` re-splitting, `--min-duration`, its emoji route) does not run for it. The look is matched to caption.py's defaults; anything beyond `color`/`bold`/`size` from the document is not styled.
- Decisions use the full-width caption box, so a face at the far side of the frame still moves the cue. Measuring the drawn width would need the font metrics libass uses.
- `mux` (several tracks) does not take `placement` yet; `render` `mode: "mux"` does.
