# ADR-008: Regional Caption Profiles, `ja-broadcast` First; Ruby and Vertical Text as Content

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 10: caption conventions as opt-in regional profiles beside the international defaults (EBU-TT-D, IMSC 1.1): speaker colours, line and character limits per language, and for scripts that use them, ruby and vertical text for burn-in and in TTML with their IMSC attributes. The first profile is `ja-broadcast`. ARIB STD-B24/B36 binary caption streams are not made (ffmpeg-skill declined them, #426: FFmpeg has no ARIB writer); the TTML of step 9 ([ADR-007](ADR-007-ttml-output.md)) replaces them. Done when a fixture with two speakers and one ruby word burns in with the profile's colours and ruby, the TTML carries both, and the same fixture without a profile uses the international defaults.

Owner rule for the ecosystem: defaults are English and international; behaviour specific to Japan is a region profile the caller selects.

## Decision

1. **`regional_profile` is a request field** of `generate`, `translate` and `render`, never derived from the document language. Without it every output is exactly what it was before this step: white text, a `Speaker: ` prefix, the locale profile's limits (ADR-001). Profiles are a table in `src/subtitle_skill/regional.py`; `contract --json` lists them with their values (`parameters.regional_profiles`). A profile names the languages it is for; `ja-broadcast` on a non-Japanese document (or translate target) is `INVALID_INPUT`.
2. **`ja-broadcast`:**
   - Speakers by colour in order of first appearance: white `FFFFFF`, yellow `FFFF00`, cyan `00FFFF`, green `00FF00`, repeating past four. The name prefix is not drawn (the colour identifies the speaker); the name moves to `metadata.speaker`. A cue's own `style.color` wins. This four-colour order is the convention of Japanese broadcast captions as it is commonly described; it was not checked against a primary guideline text in this work.
   - 15 full-width characters a line, 2 lines, where the caller's `constraints` set nothing (the ja locale default alone is 13, from the Netflix guide). The same caveat applies to the 15.
   - Colours are applied only where the output carries colour: TTML (`ttml`, `ebu-tt-d`) and a burn. SRT, WebVTT and `render` `mode: "mux"` keep the names and draw no colour (`regional_profile.colors_carried: false` in the response).
3. **Ruby is cue content, not a profile feature**: `cue.ruby: [{base, text}]`, each base found in the text in order (validated). It is written in every format: IMSC 1.1 ruby (`tts:ruby` container/base/text spans) in `ttml`; `<ruby>base<rt>reading</rt></ruby>` in WebVTT; in SRT and EBU-TT-D, which have no ruby, the reading in brackets after its base (`字幕（じまく）`, ASCII brackets outside ja/zh/ko) with a `RUBY_AS_TEXT` observation.
4. **Burn-in through subtitle-skill's ASS** (as placement, ADR-006): a profile or any ruby sends a `burn` through `formats.ass` and `caption --ass`. Speaker colours are per-event `\c` overrides (caption.py's SRT path has one colour for the whole burn). libass has no ruby, so a ruby cue is laid out by subtitle-skill: one event per line at a fixed `\pos`, and one half-size event per reading centred over its base. The base's x is estimated from East Asian Width with the advance libass gives Noto Sans CJK (0.69 x Fontsize per full-width character, measured: ten 国 at Fontsize 60 draw 407 px). Over proportional Latin text the estimate can be a few pixels off.
5. **Vertical text** (`style.vertical`): TTML region at the right edge with `tts:writingMode="tbrl"`, WebVTT `vertical:rl`. Not burned in: `UNSUPPORTED_FORMAT`. libass can draw vertical CJK only through `@`-prefixed fonts plus rotation, which this work did not verify; SRT cannot carry it either.

## Done when, and how it was checked

- Fixture `tests/fixtures/regional/ja_two_speakers_ruby.json`: two speakers (佐藤, 田中), one ruby word (字幕 / じまく).
- **Burn with `ja-broadcast`** (`tests/test_regional.py`, vendored caption.py, real ffmpeg): the second speaker's frame has yellow text and the first speaker's none; while the ruby cue is on screen there is ink above the line's own top. Checked by eye as well on a 1280x720 burn: じまく sits centred over 字幕.
- **TTML carries both**: `ttml` output with the profile has `tts:color` `#ffffff` / `#ffff00` styles and the IMSC ruby container, no names; it passes `validate_ttml` and the W3C TTML2 XSD; `parse_ttml` reads the ruby and colours back. (`ebu-tt-d` carries the colours and brackets the reading.)
- **Without a profile**: the same fixture burns white for both speakers, and its TTML has the `佐藤: ` / `田中: ` prefixes and no colour styles (ruby stays: it is content).

## Consequences

- The contract changes: `parameters.style` gains `vertical`, `parameters.cue` lists the cue fields (with `ruby`), `parameters.regional_profiles`, `capabilities.regional_profiles` and `capabilities.ruby`, `regional_profile?` in the inputs of generate/translate/render.
- `style.bold`/`italic`/`vertical` must now be booleans (before, any value was accepted and read as truthy).
- `translate` does not carry ruby across languages: the translated cue is new text.
- More profiles (for example a Korean or Chinese broadcast profile) are new entries in `regional.PROFILES`, with their own sources.
