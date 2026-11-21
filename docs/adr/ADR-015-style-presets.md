# ADR-015: Style Presets as Data

Status: Accepted (roadmap step 14; implements the design in ADR-013 part 5)

## Context

Before this step, a burn's look was one fixed default plus cue `style.color` / `bold` / `size`. A channel or a client that wants a consistent look (a font, a size, a position, an outline, a highlight colour) had no way to say so in one field. ADR-013 part 5 designed `render` `style_preset`. This ADR records how it was built and the choices made while building it.

Constraints:

- Additive and opt-in (ADR-014): a request without `style_preset` sends ffmpeg-skill the same call, keeps the same render identity and gets the same response. `style_preset` is an experimental field of the `STABLE` `render` tool (ADR-014 section 2).
- caption.py's SRT `force_style` takes only colour, weight and size for the whole burn. Font, outline, shadow, position, margin, case and a box need this skill's own ASS, which caption.py burns as it is and does not animate (ADR-011 point 4).
- Disk is tight, and a font file carries a licence. No font is bundled.

## Decision

### 1. The field

`render` `style_preset` is a bundled name or `{"path": "<workspace JSON>"}`. Any other shape is `INVALID_INPUT`, `details.field: "style_preset"`. A path goes through `PathPolicy` (inside the workspace, no traversal); the file must be JSON of at most 16 KB.

### 2. The schema (an allowlist)

| Key | Value | Default |
|---|---|---|
| `name` | required; lower-case letters, digits, hyphens, up to 40 | |
| `font` | a family, or a fallback chain of 1-6 families | the language's family (as before) |
| `size` | 2-20, percent of the frame height (the ASS `Fontsize`, so the line pitch) | 24 pt on a 288-line script (8.3 %) |
| `color`, `outline_color` | `RRGGBB` (optional `#`) | `FFFFFF`, `000000` |
| `outline`, `shadow` | 0-2, percent of the frame height | 2 pt on 288 lines, 0 |
| `case` | `none` / `upper` / `lower` | `none` |
| `position` | `bottom` (centre) / `top` (centre) / `lower-third` (bottom left) | `bottom` |
| `margin` | 0-30, percent of the frame height (vertical) | 30 pt on 288 lines |
| `bold` | `true` / `false` | `false` |
| `box` | `none` / `scrim` | `none` |
| `highlight` | `{color?, bold?}`, what `emphasis` draws | `FFD200`, bold |
| `animation` | `none` only | `none` |

- Any other key, at the top level or in `highlight`, is `INVALID_INPUT` with `details.field: "style_preset.<key>"` (`style_preset.highlight.<key>`). A bad value names its key the same way. The allowlist is checked before anything else in the file, so a `command`, a `path` or an `override` key is refused by name.
- A family name is letters, digits, spaces and `._+-'&`. A path separator, a comma (an ASS style line is comma separated), a brace, a backslash (override tags) and control characters are refused. Colours are hex only. So no string in a preset can reach the ASS as a tag.
- `animation` exists so a preset file can state it, but only `none` is accepted: a preset burns through this skill's ASS, and caption.py does not animate a given ASS.
- `box: "scrim"` is an **opaque** box in `outline_color` behind each line (libass `BorderStyle: 3`, `outline` is its padding). It is opaque because libass draws that box per glyph run: a translucent box shows darker seams where runs overlap, for example around a highlighted word (ADR-013 part 4 records the same effect).

### 3. The bundled presets

`src/subtitle_skill/presets/*.json`, each under 1 KB, each stating every field:

| Name | Font chain | Size | Colour | Case | Position, margin | Outline / shadow | Box | Highlight |
|---|---|---|---|---|---|---|---|---|
| `clean-lower-third` | DejaVu Sans, Liberation Sans, Noto Sans | 6.5 % | white | none | lower third, 8 % | 0.3 % / 0.4 % | none | `7FD4FF`, regular |
| `bold-short-form` | DejaVu Sans, Liberation Sans, Noto Sans | 9 %, bold | white | upper | bottom, 18 % (above the platforms' bottom UI) | 0.9 % / 0 | none | `FFD84A`, bold |
| `minimal-serif` | DejaVu Serif, Liberation Serif, Noto Serif | 6 % | `F0E2C0` | none | bottom, 6 % | 0.15 % / 0.25 % (`1A1A1A`) | none | `E8B04A`, regular |
| `boxed` | DejaVu Sans, Liberation Sans, Noto Sans | 6.5 % | white | none | bottom, 7 % | 0.8 % padding | opaque black | `FFD200`, bold |

### 4. Fonts

None bundled. The family is the first of the chain that `fc-list : family` lists (case-insensitive). Without fontconfig, or when no family of the chain is listed, the first family is named and libass substitutes through fontconfig. libass also falls back per glyph, so a script the family lacks (Japanese with a Latin preset) is still drawn from an installed font. `fonts_dir` (ADR-010) is an explicit field and wins: its family is named instead. The response's `style_preset.font_family` and `font_installed` say what was named and whether fontconfig listed it.

### 5. Precedence

An explicit request field overrides the preset, and the preset overrides the defaults:

- cue `style.size` (one value for the document, as before) over the preset's `size`;
- cue `style.color` and `style.bold` per cue (an explicit `bold: false` sets a bold preset back to regular for that cue);
- `emphasis.color` over the preset's `highlight.color`;
- `fonts_dir`'s family over the preset's font chain;
- a `placement` move to the top over the preset's position, for that cue.

### 6. Conflicts

| Combination | Result |
|---|---|
| `style_preset` with `mode: "mux"` | `INVALID_INPUT`, `field: style_preset`, `conflicts_with: ["mode"]` |
| `animate` (other than `none`) or `karaoke` with `style_preset` | `INVALID_INPUT`, `field: animate`/`karaoke`, `conflicts_with: ["style_preset"]` (the own-ASS rule of ADR-011 and ADR-013) |
| `style_preset` with `caption_profile: "shorts"` | refused, `conflicts_with: ["caption_profile"]`: the shorts profile sets its own size and position |
| `style_preset` with cue `ruby` | refused, `conflicts_with: ["ruby"]`: ruby is laid out at positions computed for the default look |
| a `top` preset with `placement` | refused, `conflicts_with: ["placement"]`: placement moves cues from the bottom to the top |
| `legibility` with a preset that has a box | refused, `field: legibility`, `conflicts_with: ["style_preset"]`: the box is already the treatment |
| `style_preset` with `emphasis`, `legibility` (no box), `spoken_numbers`, `cuts`, `regional_profile`, `fonts_dir`, `placement` | allowed; they share the ASS. legibility measures the box the preset lays out |

With a preset the ASS uses `WrapStyle: 0`, as for emphasis: the text is no longer re-wrapped by caption.py, so libass wraps it.

### 7. Identity and response

The render identity gets `style_preset`: the preset's values and the family named, never the file's path (what is drawn, not where it was read from). The response and the sidecar get `style_preset`: the values, `source` (`bundled` or the path as given), `font_family` and `font_installed`. Without the field none of this exists.

## Done when, and how it was checked

`tests/test_style_presets.py` (62 tests):

- **Each bundled preset burned** on a 640×360 lavfi clip through the vendored caption.py and real ffmpeg, the frame at 1 s decoded. Two lines of `oooo`: the distance between the two lines' tops is the line pitch, which libass sets from the `Fontsize` whatever the family. Measured on this machine: 23 px for 6.5 % (23.4), 32 px for 9 % (32.4), 22 px for 6 % (21.6). The test allows 10 %. The text pixels are in the preset's colour. The glyph height over the pitch is about 0.66 for `upper` (`OOOO`) and about 0.4 for lower case. The position is checked as left (`lower-third`) or centred, with the last line's bottom within the stated margin. `boxed` has box-coloured pixels above the text, and the others have the picture there.
- **A caller's file** with `position: top` burns at the top in its colour. Files with an unknown key, a font path, a family with `{\...}`, a colour with tags or an `override` key are `INVALID_INPUT` naming the key (26 cases on the validator, 3 through `render`).
- **Emphasis**: `clean-lower-third` with `emphasis: "numbers"` draws `$5,000` in its `7FD4FF`, and no pixel in the default yellow. An explicit `emphasis.color` wins. An explicit cue `style.size` wins over the preset's size on the burned frame.
- **Nothing changes without it**: `generate_ass` output is byte-for-byte the 1.0.0 output (golden files written by 1.0.0's code), the caption argv is the same `--srt` call, the identity payload has the same keys, and the second render reuses the first.
- Nothing over 1 MB is added; the four presets total about 1.4 KB.

## Consequences

- A preset burn gives up caption.py's own layout and size fitting, like every burn through this skill's ASS (ADR-006, ADR-013).
- The font named depends on what fontconfig lists on the machine that renders. The identity includes the family named, so a different machine does not reuse a render drawn with another family.
- `style_preset` is experimental (ADR-014 section 2): the schema may still grow or change in a minor release, with a CHANGELOG line.
- The contract change is additive: `render` input and output `style_preset?`, `operations.render.style_preset`, `capabilities.style_presets`, `parameters.render` gains `style_preset`, and the render description. `contract_version` is unchanged. video-production-agent re-pins before it can pass the field.
