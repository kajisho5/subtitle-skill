# ADR-010: `render` Takes a Fonts Directory and Names the Family It Holds

Status: Accepted

## Context

ffmpeg-skill's `caption` tool refuses to burn text that no installed font covers (`kind: input`: "no installed font covers Japanese text on this machine — ...") (read at 2.24.0, 3.0.0, 3.2.3 and 3.3.0). That is right: the alternative is a video of empty boxes that ffmpeg reports as a success. Its message points at `--fonts-dir`, but a `render` request had no field for one, so on a machine without a font for the subtitle's script (a venue PC, a minimal Linux box, a container) a burn failed and the only way out was to install a system font.

What `caption.py` accepts, read in its source at 1.0.0, 2.24.0 (the vendored test fixture), 3.0.0, 3.2.3 and main (3.3.0):

- `--fonts-dir DIR` ("directory with extra .ttf/.otf files"), on `caption.py` only. `overlay.py` and `graphics.py` take `--font-file` instead; this skill calls neither.
- `--font FAMILY` (a family name), also `caption.py` only.
- With `--fonts-dir` and no `--font`, a directory that covers the script (checked with `fc-scan`) passes the tool's own font check, and the burn keeps the default family.

Measured on a machine whose fontconfig knows one Latin font and no Japanese one, with a Japanese font file in the directory, against 2.24.0, 3.0.0 and 3.3.0:

| flags | caption.py reports | picture |
|---|---|---|
| none | `failed`, "no installed font covers Japanese text" | no output |
| `--fonts-dir DIR` | `completed` | empty boxes |
| `--fonts-dir DIR --font <family in DIR>` | `completed` (warning `font_lacks_script`: fontconfig does not know the family) | the text |

libass draws from the directory's files when a style names their family. It does not fall back to them for a glyph the named family lacks. Passing the directory through and nothing else would turn a clear refusal into a silent wrong picture.

## Decision

1. **`render` takes `fonts_dir`** (optional, `mode: "burn"` only): a path inside the workspace, resolved by `PathPolicy` like every other input (no absolute path, no `..`, no symlink out of the workspace; `MISSING_INPUT` when it does not exist) and required to be a directory (`INVALID_INPUT`). `mode: "mux"` with it is `INVALID_INPUT`; the `mux` operation does not take it (a soft track is drawn by the player).
2. **subtitle-skill names the family.** `subtitle_skill.fonts` reads each `.ttf` / `.otf` / `.ttc` / `.otc` directly inside the directory with the standard library: the family (`name` table, name id 1, Microsoft platform, which is what libass matches) and the code points it maps (`cmap` format 12 or 4). The family whose face has the most of the letters, digits and punctuation the burn draws (cue text, a drawn speaker name, ruby readings) is used; between equals, the family the ASS path would name for the language, then the first by file name. It is passed as `--fonts-dir DIR --font FAMILY`, and written as the `Fontname` of subtitle-skill's own ASS (placement, ruby, regional and shorts burns). `--font` goes with `--ass` too: there it only tells caption.py the font is the caller's choice.
3. **Refused before the engine runs** (`INVALID_INPUT`): a directory with no readable font, and a directory whose best family has none of the subtitle's non-ASCII characters. A family that has some but not all is used, with one `FONT_GLYPHS_MISSING` observation listing the characters (libass draws them from an installed font when one has them).
4. **A caption tool without the two flags** is `DEPENDENCY_ERROR`, decided by reading the located `caption.py` (the pattern `mux` uses for multi-track support). A request without `fonts_dir` is unaffected.
5. **The engine's refusal names the field.** When caption.py refuses a burn for a missing font and no `fonts_dir` was given, the error keeps the code (`INVALID_INPUT`) and the engine's whole message, then adds: or set `request.fonts_dir` to a workspace directory holding a font file that covers the text. The addition is short and starts with the field, so a consumer that keeps the first 500 characters of a message still has it. `details` is `{"field": "fonts_dir"}`.
6. **Provenance and identity.** The response and sidecar carry `fonts_dir: {path, family, files}`: the path as the request gave it, the family named and the files that carry it. No font bytes. The render identity includes the family and one sha256 over the names and contents of the directory's font files, so a replaced font is a new render; the path is not part of it.

## Done when, and how it was checked

- `tests/test_fonts_dir.py`, fake caption tool: the argv (`--fonts-dir <resolved> --font <family>`), the ASS `Fontname`, every path-policy refusal, the unsupported caption tool, the guided error (engine reason kept, field named), reuse and the changed-font identity. The font files are written by the test; no font binary is committed.
- Same file, real caption tool (Linux, ffmpeg): under a fontconfig that knows one Latin font and no Japanese one, a Japanese burn without `fonts_dir` fails with the engine's reason and the field; with a copy of an installed Japanese font file in `fonts_dir` it burns, through both the SRT and the ASS path. Two captions of equal length with no character in common give different frames, which empty boxes do not. With `--font` removed from the argv the SRT case of this test fails.
- It runs in `ci.yml`'s `real-engine` job (vendored 2.24.0) and in the daily compatibility check (main and v3.0.0).

## Consequences

- The font has to be inside the workspace. A caller copies the file there; the skill does not read a system font directory outside it.
- The family is chosen, not asked for. A caller who wants one family puts only that family in the directory.
- Coverage is read from `cmap`: a font that maps a character to an empty glyph counts as having it.
- Ruby and shorts layout estimate widths from the default fonts' metrics (ADR-008, ADR-009); another font can shift them slightly.
- Not checked on macOS or Windows: the real-engine test sets up its font sandbox with fontconfig on Linux. The request path, the font reader and the argv are covered on all three by the fake-engine tests.
