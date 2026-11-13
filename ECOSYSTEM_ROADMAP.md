# Roadmap — subtitle-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.9.1, version single-sourced from `pyproject.toml` (step 1). `generate` (SRT / WebVTT / TTML as IMSC 1.1 Text and EBU-TT-D, with line/cps/duration validation), `render` (burn or mux, SRT; caption placement away from faces, regional profiles, ruby and the `shorts` profile burn through subtitle-skill's ASS), `translate` and `mux` (steps 5-6). CI on 3 OSes x 2 Pythons; burn tests need ffmpeg and run where it is installed. Burned captions can move: `render` `animate` / `karaoke` pass ffmpeg-skill `caption --animate` / `--karaoke` through (step 12).

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 字幕生成・検証 | done |  |
| 字幕焼き込み | done |  |
| 字幕翻訳 | done | `translate` operation; engine chosen: Argos Translate offline by default, OPUS-MT (CTranslate2) and local Ollama LLM optional, DeepL optional, caller translation memory ([ADR-004](docs/adr/ADR-004-translation-engine.md)) |
| 動く字幕(字幕キュー由来) | done | added by the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O11): everything generated from subtitle cues, animated captions included, is this skill's; step 12. Effects timed to words in a transcript (`Karaoke`, `Emphasis`) are remotion-ffmpeg-skill's `語の時刻に合わせた字幕演出` |
| ロケール別字幕整形 | done | ja/zh/ko locale profiles: East Asian Width counting, kinsoku observations, per-locale defaults ([ADR-001](docs/adr/ADR-001-locale-profiles.md)) |

## Wiring

Agent wires generate, render burn and render's mux mode (video-production-agent#81, ADR-063); speaker labels from diarized transcripts reach the cues (video-production-agent#111). Pinned at 0.9.1 today (first at 0.4.1, video-production-agent#102). Not wired: audio_stream, `translate`, the multi-language `mux` operation (dubbing-skill step 11 needs translate), and everything from steps 8-11 (`placement`, the TTML formats, `regional_profile`, `caption_profile`, cue `ruby`/`words`, `style.vertical`); the contract changed in each of those steps; the agent has since re-pinned to 0.9.1, and which of these it now passes is not re-checked here. Delegates to ffmpeg-skill caption, probe.

## Steps

- [x] **1. Single-source the version + dependencies** — version from pyproject.toml only, `dependencies` on ffmpeg-skill `>=1.0` from one constant ([ADR-003](docs/adr/ADR-003-single-source-version.md), [#22](https://github.com/kajisho5/subtitle-skill/pull/22))
  - Done when: contract version == pyproject
- [x] **2. Align provides tool_id prefix** — `subtitle-skill/...` vs the agent's `subtitle/...`; fix or ADR ([ADR-002](docs/adr/ADR-002-provides-tool-id-prefix.md), [#20](https://github.com/kajisho5/subtitle-skill/pull/20))
  - Done when: OS registry resolves without a mapping
- [x] **3. Locale profiles** — ja/zh/ko full-width counting, kinsoku line breaking, per-locale cps defaults ([#19](https://github.com/kajisho5/subtitle-skill/pull/19))
  - Done when: Japanese fixtures
- [x] **4. Translation engine ADR** — the owner kept 字幕翻訳 here (2026-10-30); choose an offline-capable MT engine and check the licences of its code and weights ([ADR-004](docs/adr/ADR-004-translation-engine.md), [#23](https://github.com/kajisho5/subtitle-skill/pull/23))
  - Done when: the ADR is merged
- [x] **5. Machine translation** — engine chosen in the ADR, offline-capable, provenance records engine (`translate`: Argos offline default / DeepL optional / caller translation memory, timecodes kept, target-language layout and validation, glossary check; fixtures `tests/fixtures/translation/ja_en.json`, `en_ja.json`; [#24](https://github.com/kajisho5/subtitle-skill/pull/24))
  - Done when: ja<->en fixture
- [x] **6. Multi-language mux** — several subtitle documents -> one mux (`mux`: one ffmpeg-skill `caption --mode mux` call, mov_text in .mp4, SubRip in .mkv, language-tagged; `tests/test_mux.py` two-language .mkv/.mp4 and translate→mux tests; [ADR-005](docs/adr/ADR-005-multi-language-mux.md), [#24](https://github.com/kajisho5/subtitle-skill/pull/24))
  - Done when: 2-language integration test
- [x] **7. English-facing engines: Argos weight licence + commercial-safe ja→en** — the Argos ja→en package's own `metadata.json`/README state no licence (unverified, ADR-004 addendum); optional `opus-mt` (Helsinki-NLP OPUS-MT on CTranslate2, Apache-2.0 weights) and `ollama` (local Qwen3 via Ollama's HTTP API) engines, `argos` stays the default, `doctor` reports each engine's weight licence ([#25](https://github.com/kajisho5/subtitle-skill/pull/25))
  - Done when: both engines tested with mocks, no model downloaded in CI
- [x] **8. Captions placed away from faces** — given detection-skill face boxes (`detections@1`, or the reframe path of its step 9 after a crop), each cue moves from the bottom to the top safe area while a face overlaps the bottom caption box. One move per cue, never mid-cue; a minimum hold so captions do not jump between neighbouring cues; a fixed region the caller names (a sign-language window, a lower third) is avoided the same way. Burn-in writes ASS with per-event alignment and goes through ffmpeg-skill `caption --ass`; a soft track gets `{\an8}` on the moved cues. Each move is recorded with the face box that caused it. 参考：OpenChatCut（考え方のみ。コードは使わない）
  - Needs: detection-skill step 3 (faces); step 9 there for reframed output
  - Done when: a fixture with a face low in frame for 2-5 s moves only the cues in that span to the top, and qc-skill text legibility (its step 7) still passes on the burned frames
  - Done (ADR-006): `render` `placement` (`faces` detections@1, `reframe` reframe@1, `avoid` regions, `min_hold`); `tests/fixtures/placement/faces_low_2_5s.json` moves exactly the cues at 2-5 s, burned through `caption --ass` and read back from the frames; soft track gets `{\an8}`. qc-skill `video.text_legible` PASS (contrast 4.91) was run locally on the burned frames, not in CI (qc-skill is not a dependency). The face fixture is synthetic: no detector weights were downloaded
- [x] **9. TTML / IMSC 1.1 and EBU-TT-D output** — `generate` and `translate` write TTML (IMSC 1.1 Text Profile) and EBU-TT-D besides SRT / VTT, with WebVTT cue positions and region kept where the format can carry them; each output validated against its schema. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：放送局・配信プラットフォーム・海外の配給先が求める字幕形式をそのまま納品できる
  - Done when: a ja/en fixture round-trips SRT → TTML → SRT with the same cues; the TTML validates
  - Done (ADR-007): formats `ttml` (IMSC 1.1 Text) and `ebu-tt-d` for `generate`/`translate`; WebVTT positions as regions, styles by reference; every output checked against its profile before it is written (`conformance`). `tests/fixtures/ttml/ja.srt`/`en.srt` round-trip byte for byte in both profiles and validate against the W3C TTML2 XSD in CI; the EBU-TT-D XSD (no licence stated, not vendored) was run locally and passes
- [x] **10. Regional caption profiles (broadcast conventions)** — caption conventions as opt-in regional profiles beside the international defaults (EBU-TT-D, IMSC 1.1): speaker colours, line and character limits per language, and for scripts that use them, ruby and vertical text for burn-in and in TTML with their IMSC attributes. The first regional profile is `ja-broadcast` (Japanese broadcast conventions); others are added the same way. ARIB STD-B24 / B36 binary caption streams are not made: ffmpeg-skill declined them because FFmpeg has no ARIB writer ([ffmpeg-skill#426](https://github.com/kajisho5/ffmpeg-skill/issues/426)), and the TTML of step 9 is the replacement
  - Why: broadcasters and platforms in each region get the caption look they expect, without changing the defaults for everyone else（なぜ要るか：地域ごとの放送の字幕慣習に、既定を変えずに合わせられる）
  - Done when: a fixture with two speakers and one ruby word burns in with the `ja-broadcast` profile's colours and ruby, the TTML carries both, and the same fixture without a profile uses the international defaults
  - Done (ADR-008): `regional_profile` (opt-in; `ja-broadcast`: speaker colours white/yellow/cyan/green, no name prefix, 15 x 2), cue `ruby` (IMSC ruby in TTML, `<ruby>` in WebVTT, drawn over its base in burn-in, bracketed in SRT/EBU-TT-D), `style.vertical` (TTML `tbrl`, WebVTT `vertical:rl`). `tests/fixtures/regional/ja_two_speakers_ruby.json` burns with yellow for the second speaker and ruby above the line, its TTML carries colours and ruby, and without a profile it is white with name prefixes. Not done: vertical text is not burned in (`UNSUPPORTED_FORMAT`; libass vertical not verified); the colour order and 15-character limit were not checked against a primary guideline text
- [x] **11. Shorts captions** — a `shorts` profile for burned captions on vertical clips: one or two words per cue from word timings, large type inside the platform's safe zone, the speaker's face avoided (step 8)
  - なぜ要るか：縦型の切り出しに、読みやすい大きな字幕を自動で載せられる
  - Done when: a 9:16 fixture's cues sit inside the TikTok / Shorts safe zone and never exceed two words
  - Done (ADR-009): `render` `caption_profile: "shorts"` (burn, vertical video) re-cuts cues from `cue.words` into one- or two-word cues, bold at 5.5 % of the height inside the safe zone shared by TikTok / Shorts / Reels (ffmpeg-skill `_platforms.py`), faces avoided with `placement`. `tests/fixtures/shorts/vertical_words_en.json` on 9:16: seven cues, none over two words, every burned cue's pixels inside the safe zone
- [x] **12. Animated captions from cues** — `render` (burn) gains `animate: none|fade|pop|slide` and `karaoke: sweep|word`, passed through to ffmpeg-skill `caption --animate` / `--karaoke`, the flags it already has; no animation is drawn here. `karaoke` uses the cue's `words` when present and is refused without them (`INVALID_INPUT`, the field named). A burn that goes through this skill's own ASS (`placement`, `regional_profile`, `ruby`, `caption_profile: shorts`) either carries the animation into that ASS or is refused with the reason, recorded in an ADR, never silently static; a soft track (mux) with an animation is refused, since only a burn can move. The values used are written in the result. [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O11)
  - Why: pop-in and karaoke captions for shorts and talks come from the same subtitle document, with no Node or Remotion project（なぜ要るか：同じ字幕データから、Node や Remotion なしで動く字幕を焼き込める）
  - Needs: nothing new from ffmpeg-skill (existing flags); a contract change, so the agent re-pins before it can pass them
  - Done when: a fixture burns once per `animate` mode and once with `karaoke`, and decoded frames show the caption absent / partial / full at the expected frames of a cue; `karaoke` without words and `animate` on a mux are refused; a burn with no `animate` sends ffmpeg-skill the same call as today
  - Done ([ADR-011](docs/adr/ADR-011-animated-captions.md), PR_LINK): `render` `animate` (none/fade/pop/slide) and `karaoke` (sweep/word) become `caption --animate` / `--karaoke --karaoke-style … --karaoke-timing even`; karaoke words come from `cue.words` (also written beside caption.py's `_adjusted.srt`, where it looks after lengthening a cue). `tests/test_animation.py` burns `tests/fixtures/animation/talk_words_en.json` on a 320×180 lavfi clip once per mode and per karaoke style and reads the frames back (absent on the cue's first frame, partial in the entrance, full after it; highlight following the words). Refused with the field named: karaoke without words, any animation on a mux or through this skill's own ASS (`placement`, `regional_profile`, `ruby`, `caption_profile`: caption.py does not animate a given ASS, and it is not re-drawn here), and `animate` with `karaoke: word` (caption.py fades the caption out after the first word). No field, or `animate: none`, sends the same argv and reuses the same render. Contract change additive; the agent has to re-pin before it can pass the fields

## Boundaries

Under the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O11): animated captions made from subtitle cues are this skill's (step 12); caption effects timed to words in a transcript (remotion-ffmpeg-skill `Captioned --words`, `Karaoke`, `Emphasis`) are remotion-ffmpeg-skill's; single timed text elements (sticker, hook) are motion-graphics-skill's. Transcription exports SRT/VTT as views. dubbing-skill takes translated cues from `translate` (its step 11).

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
