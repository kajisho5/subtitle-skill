# ADR-001: Locale Profiles for Japanese, Chinese and Korean Subtitles

Status: Accepted

## Context

Before this change, validation counted every character as one, with one set of defaults for every language: 42 characters per line and 20 characters per second. Those two numbers are the English (USA) limits in Netflix's style guide (see the table below), so they fit English text. They are wrong for Japanese, Chinese and Korean:

- A line of 42 Japanese characters is more than three times the Japanese limit, so a far too long line produced no warning.
- Half-width Latin letters inside Japanese text count as half a character, not one.
- Some characters may not begin or end a line (禁則処理, kinsoku shori), and nothing checked this.

`ECOSYSTEM_ROADMAP.md` step 3 asks for per-locale profiles. Every default below was read from the primary page on 2026-10-30. Where a page did not give a value, this ADR says so, and the default chosen in its place is the stricter one.

## Decision

1. **A profile is chosen by the primary subtag of `SubtitleDocument.language`.**
   - `ja`, `ja-JP` → `ja`; `zh`, `zh-Hans`, `zh-TW` → `zh`; `ko`, `ko-KR` → `ko`.
   - Every other language keeps the generic behaviour exactly as it was: `len()` counting, the 42 / 20 defaults, no kinsoku check, and the same observation messages.
   - The profiles live in `src/subtitle_skill/locales.py`.
2. **Line length and reading speed are counted in full-width units, using the Unicode East Asian Width property ([UAX #11](https://www.unicode.org/reports/tr11/), revision 46).**
   - Wide (`W`) and fullwidth (`F`) characters count as 1.
   - Ambiguous (`A`) characters also count as 1. UAX #11 §5 says ambiguous characters follow their context, and names the language tag as one such context. A ja/zh/ko document is an East Asian context.
   - Narrow (`Na`), halfwidth (`H`) and neutral (`N`) characters count as the profile's `half_width_weight` (see the table below).
   - Combining marks (`Mn`, `Me`) and format characters (`Cf`) count as 0, so `か` + U+3099 counts the same as `が`.
   - Line breaks are not counted.
   - Reading speed uses the same weighted count. The style guides give their limits in "characters per second" and never define a separate count for it.
3. **Defaults per profile.** These apply only when the caller sets no value for that field in `constraints`; a caller's value always wins. `max_lines` (2) and `min_duration` (0.5 s) stay generic: every guide below allows at most two lines, and the Japanese guide's minimum of 500 ms equals the existing default. `max_duration` stays at the generic 10 s. None of the language guides below sets a maximum duration.

   | Profile | Characters per line | Reading speed (cps) | Half-width weight | Source |
   |---|---|---|---|---|
   | `ja` | 13 | 4 | 0.5 | Netflix [Japanese Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517) (change log up to 2025-12-19): "Horizontal subtitles - Maximum 13 full-width characters per line"; "Half-width character, space, and punctuation counts as 0.5 characters"; reading speed "Up to 4 characters per second". |
   | `zh` | 16 | 9 | **1 (conservative, unverified)** | Netflix [Chinese (Simplified)](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007) (2025-07-04) and [Chinese (Traditional)](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215994807) (2025-12-19) guides: "16 characters per line"; "Adult programs: Up to 9 characters per second". Neither guide says how half-width characters count, so each one counts as a full character. This is the stricter choice, and it is marked as unverified. |
   | `ko` | 16 | 12 | 0.5 | Netflix [Korean Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/216001127) (2025-07-07): "16 characters per line"; "Latin characters, spaces, punctuation count as 0.5 character"; "Adult programs: Up to 12 characters per second". |
   | generic (unchanged) | 42 | 20 | 1 | Netflix [English (USA) Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977) (2025-12-19): "42 characters per line"; "Adult programs: Up to 20 characters per second". |

   - The `zh` and `ko` speeds are the adult values. The children's values (zh 7, ko 9) and the SDH values are not separate profiles. A caller that needs them passes `reading_speed_cps`.
   - The Japanese page gives a single speed (4 cps) for subtitles, with no separate adult or children's value. Its SDH limits (16 characters per line, 7 cps) are not used.
   - The `ko` half-width weight applies East Asian Width the same way it does for Japanese. The Korean guide's wording names "Latin characters, spaces, punctuation". A full-width punctuation mark (rare in Korean subtitles) therefore counts as 1 here, which is the stricter reading.
4. **Kinsoku character classes.** JIS X 4051 itself is a paid standard and was not read. The classes come from W3C's public descriptions of it and of the Chinese and Korean rules:
   - **Japanese.** [JLREQ](https://www.w3.org/TR/2020/NOTE-jlreq-20200811/) (W3C Working Group Note, 2020-08-11). JLREQ says it is "mainly based on" JIS X 4051:2004. The code points are copied from its Appendix A class tables.
     - **May not start a line (§3.1.7, 行頭禁則):** closing brackets cl-02 (」』）〕］｝〉》】 …), hyphens cl-03 (‐ 〜 ゠ –), dividing punctuation cl-04 (！？‼⁇⁈⁉), middle dots cl-05 (・：；), full stops cl-06 (。．), commas cl-07 (、，), iteration marks cl-09 (ヽヾゝゞ々〻), the prolonged sound mark cl-10 (ー) and small kana cl-11 (ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ and ㇰ–ㇿ).
     - **May not end a line (§3.1.8, 行末禁則):** opening brackets cl-01 (「『（〔［｛〈《【 …).
     - **Strict form.** A JLREQ note allows 々, ー and small kana to start a line, and many books do. These relaxations are not applied, because the roadmap asks for small kana and ー to be prohibited.
     - **Compatibility forms.** JLREQ lists ASCII code points (e.g. U+0028). Its appendix note says Japanese text normally uses the compatibility counterpart (U+FF08 FULLWIDTH LEFT PARENTHESIS). So every code point in U+FF01–U+FFEE whose NFKC form is a class member joins that class. This covers fullwidth punctuation and the halfwidth katakana forms such as ｡ ｣ ､ ･ ｰ ｯ.
     - Warichu brackets (cl-28/29) do not occur in subtitles and are left out. Only U+31F7 is kept from the cl-11 sequence U+31F7 U+309A.
   - **Chinese.** [CLREQ](https://www.w3.org/TR/clreq/) §6.1.1, "basic" rule set, which it calls "the most recommended method".
     - **May not start a line:** pause and stop marks 、，。：；！？, closing quotation marks, brackets and book-title marks (the JLREQ cl-02 set), interpuncts (U+00B7, plus U+30FB), and solidi (U+002F, U+FF0F). The solidi are named as 分隔号 in the rule's Chinese text.
     - **May not end a line:** opening quotation marks, brackets and book-title marks (the JLREQ cl-01 set).
     - **Not included:** connector marks (连接号). CLREQ names them but gives no code-point list next to the rule, and none was verified. This is a known gap.
   - **Korean.** [KLREQ](https://www.w3.org/TR/klreq/) (W3C Group Note Draft, 2026-03-21) §7.1.2 / §7.1.3.
     - **May not start a line:** closing parentheses cl2, hyphens cl5, dividing punctuation cl6, middle dots cl7, periods and commas cl8–9, iteration marks cl11 and the prolonged sound mark cl12.
     - **May not end a line:** opening parentheses cl1.
     - The KLREQ class tables were not read. Each class is represented by the JLREQ class of the same name, which is an interpretation.
     - KLREQ §7.1.1 allows breaking by character or by word (어절). The `ko` profile breaks at spaces first.
5. **Observation, never correction.** This follows `validation.py`'s design rule.
   - Kinsoku is checked only at existing line breaks: the start of every line after the first, and the end of every line before the last. A violation is reported as the warning `KINSOKU_LINE_START` or `KINSOKU_LINE_END`.
   - When a cue of a profiled language has a `LINE_TOO_LONG` or kinsoku observation, `locales.break_lines` computes a re-break. It works greedily and moves the break back (追い出し) until no line starts or ends with a prohibited character.
   - If the result fits `max_lines` and the line length, and breaks no kinsoku rule, its text is attached to those observations as `suggested_text`.
   - The cue text and the generated file never change. Whether to use the suggestion is the caller's decision.
6. **The contract does not change.** `contract --json` is byte-for-byte unchanged, apart from the version that release automation bumps.
   - video-production-agent pins this contract and treats any change to `parameters`, `capabilities` or `operations` as fatal drift (its `FATAL_DRIFT_KEYS`). A fatal drift turns the subtitle Skill to MISSING.
   - A profile is therefore chosen from the existing required field `language`. The new observation codes and the optional `suggested_text` key are additive members of the existing `observation` list.
   - If profiles are advertised in the contract later, that has to be coordinated with the agent's pinned snapshot.

## Consequences

- Japanese, Chinese and Korean documents now produce `LINE_TOO_LONG` and `READING_SPEED_TOO_HIGH` warnings at the limits a professional guide uses. Kinsoku problems are reported for these three languages only. Japanese text in a document tagged `en` is still counted generically.
- These are observations (warnings), so no request that succeeded before now fails. A caller that sets `max_chars_per_line` or `reading_speed_cps` still gets exactly those thresholds.
- Line breaking and its suggestion are part of the observations only. Rewriting cues stays outside this Skill's mandate, as listed in README "Limitations".
- Known gaps:
  - the half-width weight for Chinese is unverified (set conservatively to 1);
  - Chinese connector marks are not in the kinsoku classes;
  - the Korean classes are mapped through JLREQ;
  - vertical subtitles (Japanese: 11 characters per line) are not modelled, because subtitle-skill has no vertical layout.
- `tests/fixtures/locale/ja_line_breaking.json` and `ja_validation.json` are the acceptance fixtures. Their expected values were worked out by hand from the rules above.
