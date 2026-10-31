# Roadmap — subtitle-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

pyproject 0.1.2 but `__version__` 0.1.0. `generate` (SRT/VTT with line/cps/duration validation) and `render` (burn or mux, SRT). 106 tests, CI on 3 OSes x 2 Pythons.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 字幕生成・検証 | done |  |
| 字幕焼き込み | done |  |
| 字幕翻訳 | done | `translate` operation; engine chosen: Argos Translate offline by default, OPUS-MT (CTranslate2) and local Ollama LLM optional, DeepL optional, caller translation memory ([ADR-004](docs/adr/ADR-004-translation-engine.md)) |
| ロケール別字幕整形 | done | ja/zh/ko locale profiles: East Asian Width counting, kinsoku observations, per-locale defaults ([ADR-001](docs/adr/ADR-001-locale-profiles.md)) |

## Wiring

Agent wires generate and render (burn); render's mux mode, audio_stream, translate and mux are not wired (translate/mux change the contract's operations: the agent must re-pin). Delegates to ffmpeg-skill caption, probe.

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
- [x] **7. English-facing engines: Argos weight licence + commercial-safe ja→en** — the Argos ja→en package's own `metadata.json`/README state no licence (unverified, ADR-004 addendum); optional `opus-mt` (Helsinki-NLP OPUS-MT on CTranslate2, Apache-2.0 weights) and `ollama` (local Qwen3 via Ollama's HTTP API) engines, `argos` stays the default, `doctor` reports each engine's weight licence ([PR_LINK])
  - Done when: both engines tested with mocks, no model downloaded in CI

## Boundaries

Animated captions also exist in remotion (Captioned/Karaoke); transcription exports SRT/VTT as views. dubbing depends on translation.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
