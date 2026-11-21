# ADR-013: Caption Quality for Talking-Head Video

Status: Accepted (parts 1-4 implemented; part 5 implemented in roadmap step 14, see ADR-015; part 6 is an evaluation only)

## Context

Burned captions on talking-head video (interviews, talks, short clips cut from them) look worse than they need to in six ways, none of which `render` addressed:

1. A cue that stays on screen across a hard cut jumps from one shot to the next and reads as a mistake.
2. Spoken numbers come out of a transcription as words ("five thousand dollars"). On screen a reader expects `$5,000`, and a number broken across two lines or two shorts chunks is hard to read.
3. Nothing in a caption stands out. One highlighted word (an amount, a name) helps a viewer who is reading fast, but only if it is chosen consistently and can be changed.
4. White text over a bright patch of the picture disappears. A permanent box behind every caption fixes that and makes clean footage worse.
5. The look (font, size, colours, case, position) is one fixed default plus `style.color` / `bold` / `size`.
6. Text placed behind the speaker (the person drawn over the caption) is a common effect for short clips.

Constraints that shape the decisions:

- Every field is additive and opt-in. A request with none of them must send ffmpeg-skill the same call, keep the same render identity (an earlier render is reused), and return the same response.
- ffmpeg-skill `caption` burns an SRT with one whole-burn `force_style`, and re-wraps the SRT's text itself, treating every Unicode space (U+00A0 included) as a break opportunity (its `_common/wrap.py` uses `str.isspace`). With `--ass` it burns the given file as it is and ignores `--animate` / `--karaoke` (ADR-011).
- This skill already writes its own ASS for per-event layout (placement, regional profiles, ruby, shorts; ADR-006, -008, -009).
- Hard cuts are measured by media-analysis-skill (`scene_detection`: ffmpeg `scdet`, threshold 10 by default, `min_scene_duration` 0.5 s). Its result is a `media-analysis/response@1` whose `results[].observation` has `kind: "scene_detection"`, `data.cuts[] {frame, time, score}`, `data.scenes[] {index, start, end, ...}`, and `asset.fingerprint` (the file's sha256).

## Decision

### 1. Cut-aware cues (`render` `cuts`)

- `cuts: {"shots": "<workspace path>"}` reads the cut times from a media-analysis `scene_detection` result: a whole `response@1` (the first ok result of that kind) or the one observation. The scene starts after the first are the cuts (they honour its `min_scene_duration`); `data.cuts` is used when there are no scenes. A result whose `asset.fingerprint` is not this video's sha256 is refused (`INVALID_INPUT`, `details.field` `cuts.shots`).
- `cuts: {"detect": true, "threshold"?: 10, "min_scene"?: 0.5}` measures them here with the same filter (`scdet`, decode only, `metadata=print`), for a caller without a media-analysis result. Exactly one source; `threshold` / `min_scene` only with `detect`.
- A cut more than 0.04 s (one frame at 25 fps) inside a cue changes it with one of three moves. `min_duration` defaults to the request's `constraints.min_duration` (0.5 s by default):
  - **split** when both sides are at least `min_duration` and the text divides: two cues (`<id>.a`, `<id>.b`) meeting at the cut. With `words`, the words whose midpoint is before the cut go first. Without them, the text is divided at the space whose share of characters is nearest the share of time. A no-break space never divides.
  - **start moved** to the cut when the part before it is too short, and **end moved** when the part after it is.
  - Neither side long enough: the cue is left as it is, listed in `unresolved` and observed as `CUE_SPANS_CUT`. A short cue across a cut is better than an unreadable flash.
- With `caption_profile: "shorts"` the moves apply to the chunks, after chunking.
- Works for burns and soft tracks: it is timing, not drawing.
- The response's `cuts` gives the source, `cut_times`, `min_duration`, every change (`cue_id`, `action`, `cut`, `from`, `to`) and `unresolved`. The cue texts and times after the change are in the render identity (through the document), together with the cut times.

### 2. Spoken numbers as one token (`spoken_numbers: "en"`, `generate` and `render`)

- A fixed English rule set (`numbers.py`), not a model: amounts (`$5,000`, `€40`, `$2.5 million`), percentages (`40%`, also "40 per cent"), years ("nineteen ninety-nine", "twenty twenty-four", "twenty oh five"; "two thousand and eight" only after a year word such as "in" or "since"), dates ("May fifth" → `May 5`, "March 3rd, twenty twenty-four" → `March 3, 2024`), and other spelled cardinals of ten and more, and decimals. One to nine stay words, as most style guides write them. Digits already written stay; only a unit word after them is folded in. A month name counts only capitalised and followed by a day, so "you may go" is untouched. Anything not recognised is left exactly as written.
- `true` means `"en"`; other locales are opt-in values added to `numbers.LOCALES` as their rules are written. None exists yet.
- **One token:** a space inside a written form is U+00A0, and the cue's `words` for the phrase are merged into one word (first start, last end). The cut-aware split and the shorts chunker divide only between words and at ordinary spaces, so a cue is never split inside an amount. libass does not break a line at U+00A0 (checked: with `WrapStyle: 1` on a 320 px frame, "NINE THOUSAND" with a plain space broke across lines in two of eight widths, with U+00A0 in none). ffmpeg-skill `caption` does break there when it re-wraps an SRT, so a burn with a written form that contains U+00A0 goes through this skill's own ASS. A written form without a space (`$5,000`, `40%`) keeps the SRT path and the ffmpeg-skill call shape.
- A cue with ruby is not rewritten (a ruby base must stay findable in the text).
- The response's `spoken_numbers` lists every replacement (`cue_id`, `spoken`, `written`).

### 3. Emphasis (`render` `emphasis`, burn only)

- Modes: `off` (default), `auto`, `numbers`, and `{"mode": "words", "words": [...]}`. A `words` entry is a word (marked in every cue that has it) or `{cue_id, word, index?}`. An optional `color` (default `FFD200`) is the highlight colour.
- `auto` ranks the cue's tokens: a number or amount (a digit, or `$ € £ ¥ %`), then a proper name (capitalised, not starting a sentence, not "I"), then a contrast word (`but`, `however`, `instead`, `never`, `not`, `only`, ...). Ties go to the earlier token. `numbers` keeps the first class only.
- At most one mark per cue, two on a cue of eight words or more. A filler (`um`, `like`, `just`, `really`, `actually`, articles, ...) is never marked; an explicit one is listed in `skipped` with the reason. A cue with ruby is not marked.
- The response's `emphasis.marks` are `{cue_id, word, index, reason}`. That is the `words` entry shape, so a person or an agent can edit the automatic choice and send it back.
- **Rendering:** the marked token (its edge punctuation excluded) is drawn in the highlight colour and bold, then set back to the cue's own colour and weight with explicit override tags (not `\r`, which would drop a regional profile's speaker colour). This needs per-word overrides, which caption.py's SRT `force_style` cannot express, so an emphasised burn goes through this skill's own ASS.

### 4. Automatic legibility (`render` `legibility: "auto"`, burn only)

- For each cue (or shorts chunk), the box its text is drawn in is estimated from the layout the ASS writer uses: the caption box for the cue's position, the text width from the shorts module's advance estimate, the line count from the text and the frame width. Three frames are sampled (at 20 %, 50 % and 80 % of the cue), cropped to the box, scaled to at most 96 px wide and read as RGB with ffmpeg (decode only).
- Measured per frame: the background's relative luminance (WCAG 2, sRGB linearised) at its 90th percentile, and the spread between the 10th and 90th percentile. The worst frame is used. The contrast is the WCAG ratio of the text colour against that luminance.
- Rule: contrast under 3.0 → **scrim** (a rectangle at 62 % opacity on layer 0 under the text). Under 4.5, or a spread over 0.45 → **halo** (outline 2.5× and blur 1.5× the default outline, the same dark colour). Otherwise nothing is added. Text darker than mid-grey is measured and never treated.
- The scrim is a drawn rectangle, not `BorderStyle: 3`: libass draws that box per glyph run, so a highlighted word inside it left darker seams where the boxes overlap.
- The response's `legibility.cues[]` gives, per cue, `treatment`, `reason`, `contrast`, `background_luminance`, `spread`, the `box` measured and `sampled_at`. A scrim also gets `contrast_with_scrim`, estimated as libass blends (in encoded values).
- Clean footage is untouched: on a dark picture every cue is `none`, and the burn is byte for byte the burn without `legibility`.

### Conflicts and the ASS path

| Combination | Result |
|---|---|
| `emphasis` or `legibility` with `mode: "mux"` | refused, `INVALID_INPUT`, `conflicts_with: ["mode"]` (only a burn draws) |
| `emphasis`, `legibility`, or a `spoken_numbers` form with U+00A0, with `animate` / `karaoke` | refused, `conflicts_with` names them: they burn through this skill's ASS, which caption.py does not animate (ADR-011, point 4) |
| `spoken_numbers` with no spaced form, with `animate` / `karaoke` | allowed: the SRT path is kept |
| any of them with `placement`, `regional_profile`, ruby, `caption_profile`, `fonts_dir` | allowed: they share the ASS |
| `cuts` with anything | allowed, including mux and karaoke (the words are split with the cue) |

When one of emphasis, legibility or a spaced number sends a burn to this skill's ASS, that ASS uses `WrapStyle: 0`: libass wraps long lines and never breaks at U+00A0. Without them the ASS is the one written before, with `WrapStyle: 2`, so placement and ruby burns are unchanged.

### Unchanged without the fields

None of the code above runs unless its field is present. Without them the argv sent to caption.py, the identity payload's keys and the response are as before. `emphasis: "off"` and `legibility: "off"` are the same as absent and reuse an earlier render. `tests/test_caption_quality.py` checks all three with a recording fake caption.py. The rest of the suite (placement, ruby, shorts, animation) passes unchanged.

### 5. Style presets as data (implemented in roadmap step 14: [ADR-015](ADR-015-style-presets.md) records the build and its choices)

Design, so the step can be done without re-deciding:

- `render` `style_preset`: a bundled name, or `{"path": "<workspace JSON>"}`. A preset is a JSON object validated against an allowlist: `name`, `font` (family), `size` (percent of the frame height), `color`, `outline_color`, `outline` (percent of the height), `shadow`, `case` (`none` / `upper` / `lower`), `position` (`bottom` / `top` / `lower-third`), `margin` (percent of the height), `bold`, `box` (`none` / `scrim`), `highlight` (`{color, bold}`, used by `emphasis`), and `animation` (`none` only while a preset burns through this skill's ASS, see point 4 of ADR-011). Any other key is `INVALID_INPUT`. A preset never carries a command, a path or override tags.
- First presets: `clean-lower-third`, `bold-short-form`, `minimal-serif`, `boxed`.
- Fonts: none bundled. A preset names a family and a fallback chain of families commonly installed (DejaVu, Liberation, Noto) and resolved through fontconfig, or the caller's `fonts_dir`. A font is bundled only if it is SIL OFL or Apache-2.0 and the size cost is recorded.

### 6. Text behind the speaker (person matte): evaluation only

How it would work: a per-frame mask of the person, then three layers composited: the picture, the caption over it, and the person cut out by the mask over the caption.

| Option | Model / disk | Cost (CPU) | Notes |
|---|---|---|---|
| Small portrait-segmentation network (256×256 input) | ~0.5-2 MB weights, Apache-2.0 class licences exist | ~5-15 ms a frame; a 60 s 30 fps clip ≈ 10-30 s | Coarse edges (hair, hands); flicker without temporal smoothing |
| Video matting network with recurrent state | ~15-30 MB weights; licences vary (several are GPL) | ~50-150 ms a frame at 512 px on CPU; minutes a clip | Clean hair edges, temporally stable; a GPU makes it practical |
| General promptable segmentation model | hundreds of MB to GB | seconds a frame on CPU | Far beyond this skill's disk and time budget |
| Chroma key (ffmpeg's own filters) | none | real time | Works only on a green or blue screen |

Shared cost besides the model: decoding every frame, writing the matte as a lossless grey video (roughly 10-50 MB a minute at 1080p), and a second encode with an alpha merge and overlay.

Where it fails: hair and motion blur (a halo or a bitten edge around the head), hands and held objects that are not "person", several people (which one is in front of the text?), a person covering most of the caption (the text is not readable, so placement must move it first), low light and busy backgrounds (the mask flickers), and partial bodies at the frame edge.

Decision: not built here. A person mask is a detection job (detection-skill owns faces and would own a `matte@1` document), and the composite is ffmpeg-skill's (overlay, alpha merge). If both exist, this skill's part is small: draw the caption as its own layer and say which layer goes behind. The disk budget of this environment (a few hundred MB shared) rules out the recurrent matting models today. The small portrait network is the realistic first candidate, with temporal smoothing and placement run first.

## Done when, and how it was checked

`tests/test_caption_quality.py` (66 tests): unit tests for each rule, and real burns through the vendored caption.py and ffmpeg on lavfi clips 320×180 at 25 fps, read back frame by frame:

- **Hard cut mid-cue** (dark 0-1.5 s, bright 1.5-3 s; `scdet` puts the cut on frame 38, 1.52 s): a 0.4-2.6 s cue is split there. The first part's highlighted `$5,000` is on the frame before the cut and on no frame after it. The second part is drawn from the cut on. The same split comes from a media-analysis `scene_detection` document.
- **Bright and dark backgrounds**: the dark-shot cue measures contrast > 15 and gets nothing; the bright-shot cue measures < 1.5 and gets a scrim (estimated 6.9 against the box). On the frames the box corner is below 120 of 255 while the picture beside it stays above 230. A mid-grey picture (contrast about 3.9) gets a halo. On an all-dark clip the burn with `legibility` is byte-identical to the burn without it.
- **Number phrases**: 18 spoken → written pairs and 5 non-numbers. Word timings merged. On a caption that wraps onto two lines, the highlighted `$2.5 million` stays on one line.
- **Shorts on a 9:16 cut**: no chunk spans the cut ("$5,000" is one word, so its chunk moves its start to the cut instead of splitting inside the amount); emphasis and legibility apply per chunk.
- **Unchanged**: no field, or `emphasis`/`legibility` `"off"`: the same `--srt` argv, the same identity keys, reuse.

## Consequences

- An emphasised or legibility-checked burn gives up caption.py's own layout (its re-wrap and size fitting) for libass's wrap in this skill's ASS, like the placement path. A very long cue can still be wide.
- The legibility box is an estimate from character counts (0.6 em per Latin character). A proportional font can draw slightly wider or narrower than the scrim; the box has half a line of padding.
- `cuts.detect` and `legibility` run ffmpeg directly, read-only, with an argv built here from fixed parts. ffmpeg-skill's vendored test fixture has only `caption` and `probe`, and media-analysis-skill is the preferred source of cuts.
- The English number rules are heuristics: "May" is a month only when capitalised and followed by a day, a year spelled with "thousand" needs a year word before it, and "twelve hundred" is 1,200, not a year.
- The contract change is additive: `render` inputs `cuts?`, `spoken_numbers?`, `emphasis?`, `legibility?` and the same outputs; `generate` input and output `spoken_numbers?`; `operations.render.cuts` / `spoken_numbers` / `emphasis` / `legibility`; four `capabilities`; four `parameters.render` names; observation `CUE_SPANS_CUT`. `contract_version` is unchanged. video-production-agent re-pins before it can pass the fields.
