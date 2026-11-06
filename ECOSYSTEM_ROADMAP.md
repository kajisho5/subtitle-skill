# Roadmap — subtitle-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

0.4.1, version single-sourced from `pyproject.toml` (step 1). `generate` (SRT/VTT with line/cps/duration validation), `render` (burn or mux, SRT), `translate` and `mux` (steps 5-6). 106 tests, CI on 3 OSes x 2 Pythons.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 字幕生成・検証 | done |  |
| 字幕焼き込み | done |  |
| 字幕翻訳 | done | `translate` operation; engine chosen: Argos Translate offline by default, OPUS-MT (CTranslate2) and local Ollama LLM optional, DeepL optional, caller translation memory ([ADR-004](docs/adr/ADR-004-translation-engine.md)) |
| ロケール別字幕整形 | done | ja/zh/ko locale profiles: East Asian Width counting, kinsoku observations, per-locale defaults ([ADR-001](docs/adr/ADR-001-locale-profiles.md)) |

## Wiring

Agent wires generate, render burn and render's mux mode (video-production-agent#81, ADR-063); speaker labels from diarized transcripts reach the cues (video-production-agent#111). Pinned at 0.4.1 (video-production-agent#102). Not wired: audio_stream, `translate` and the multi-language `mux` operation (dubbing-skill step 11 needs translate). Delegates to ffmpeg-skill caption, probe.

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
- [ ] **9. TTML / IMSC 1.1 and EBU-TT-D output** — `generate` and `translate` write TTML (IMSC 1.1 Text Profile) and EBU-TT-D besides SRT / VTT, with WebVTT cue positions and region kept where the format can carry them; each output validated against its schema. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：放送局・配信プラットフォーム・海外の配給先が求める字幕形式をそのまま納品できる
  - Done when: a ja/en fixture round-trips SRT → TTML → SRT with the same cues; the TTML validates
- [ ] **10. Regional caption profiles (broadcast conventions)** — caption conventions as opt-in regional profiles beside the international defaults (EBU-TT-D, IMSC 1.1): speaker colours, line and character limits per language, and for scripts that use them, ruby and vertical text for burn-in and in TTML with their IMSC attributes. The first regional profile is `ja-broadcast` (Japanese broadcast conventions); others are added the same way. ARIB STD-B24 / B36 binary caption streams are not made: ffmpeg-skill declined them because FFmpeg has no ARIB writer ([ffmpeg-skill#426](https://github.com/kajisho5/ffmpeg-skill/issues/426)), and the TTML of step 9 is the replacement
  - Why: broadcasters and platforms in each region get the caption look they expect, without changing the defaults for everyone else（なぜ要るか：地域ごとの放送の字幕慣習に、既定を変えずに合わせられる）
  - Done when: a fixture with two speakers and one ruby word burns in with the `ja-broadcast` profile's colours and ruby, the TTML carries both, and the same fixture without a profile uses the international defaults
- [ ] **11. Shorts captions** — a `shorts` profile for burned captions on vertical clips: one or two words per cue from word timings, large type inside the platform's safe zone, the speaker's face avoided (step 8)
  - なぜ要るか：縦型の切り出しに、読みやすい大きな字幕を自動で載せられる
  - Done when: a 9:16 fixture's cues sit inside the TikTok / Shorts safe zone and never exceed two words

## Boundaries

Animated captions also exist in remotion (Captioned/Karaoke); transcription exports SRT/VTT as views. dubbing-skill takes translated cues from `translate` (its step 11).

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
