# ADR-009: Shorts Captions Are a `render` Caption Profile Over Word Timings

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 11: a `shorts` profile for burned captions on vertical clips: one or two words per cue from word timings, large type inside the platform's safe zone, the speaker's face avoided (step 8, [ADR-006](ADR-006-caption-placement.md)). Done when a 9:16 fixture's cues sit inside the TikTok / Shorts safe zone and never exceed two words.

ffmpeg-skill `scripts/_platforms.py` (vendored 2.24.0; the same table on main) gives each destination's safe zone as the fraction of the frame each edge's UI covers: TikTok top 0.10, bottom 0.22, left 0.05, right 0.14; YouTube Shorts 0.06 / 0.18 / 0.05 / 0.12; Instagram Reels 0.08 / 0.20 / 0.05 / 0.12.

## Decision

1. **`render` takes `caption_profile: "shorts"`**, burn only, vertical video only (height > width after rotation); anything else is `INVALID_INPUT`. It is a layout profile, separate from `regional_profile` (ADR-008); both can be given.
2. **Word timings are cue content**: `cue.words: [{text, start, end}]`, inside the cue, in order, not overlapping (validated). `shorts` needs them on every cue (`MISSING_INPUT` otherwise); it does not guess word times from text.
3. **Chunks**: at most two words; a word ending a clause (`.,!?;:…` and `。、！？；：`) ends its chunk; a cue boundary ends a chunk. A chunk shows from its first word's start to its last word's end, held to the next chunk's start when that gap is under 0.3 s. Words are joined with a space, without one for ja/zh. The chunk id is `<cue id>.<n>`; the source cue id (and speaker, since a name prefix would be a third word) go to the chunk's metadata.
4. **Safe zone**: the intersection of the three destinations, edge by edge the largest margin: top 10 %, bottom 22 %, left 5 %, right 14 %. Taken from ffmpeg-skill's table (cited, copied as constants: ffmpeg-skill is not imported).
5. **Type and position**: bold, Fontsize 5.5 % of the frame height (106 px at 1920), outline 0.4 %, centred horizontally in the safe zone (not the frame: the right edge's UI column is wider), bottom edge at 70 % of the height. A chunk whose estimated width exceeds 90 % of the safe width is drawn smaller. The estimate uses 0.6 x Fontsize per Latin character and 0.69 per full-width character, measured on libass burns (DejaVu Sans Bold: 48.5 px a character for "Hello world" at Fontsize 100, 87.9 for "mmmmmmmmmm").
6. **Faces**: with `placement`, ADR-006's rule runs on the chunks with the shorts boxes (bottom: the caption line; top: the top of the safe zone).
7. **Provenance**: the response and sidecar carry `caption_profile: {name, cues: [{id, source_cue, start, end, text, words}], max_words, safe_zone, font_size_px}`; the profile is part of the render identity.

## Done when, and how it was checked

- `tests/fixtures/shorts/vertical_words_en.json` (three cues, 12 words, one pair of long words) on a 360x640 video: seven chunks, none over two words; each chunk's estimated box is inside the safe zone at 360x640 and 1080x1920; burned through the vendored caption.py, the bright pixels of every chunk's middle frame lie inside the safe zone (`tests/test_shorts.py`). A 1080x1920 burn was also checked by eye with the safe zone drawn over it.
- With `tests/fixtures/shorts/faces_9x16.json` (a face over the caption line from 4.0 s), the chunks on screen then move to the top of the safe zone.

## Consequences

- Word timings must come from the caller (transcription-skill's word output); there is no fallback that splits text evenly over time.
- The shorts layout ignores the document's `style.size`/`position`/`align`; colours (a cue's own or a regional profile's) still apply.
- The width estimate can be wrong for an unusual font; the safety margin (90 %) and the burn test cover the default fonts, not every font a system may substitute.
