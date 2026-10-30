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
| 字幕翻訳 | missing | owned here (owner decision 2026-10-30); engine not chosen yet |
| ロケール別字幕整形 | done | ja/zh/ko locale profiles: East Asian Width counting, kinsoku observations, per-locale defaults ([ADR-001](docs/adr/ADR-001-locale-profiles.md)) |

## Wiring

Agent wires generate and render (burn); mux and audio_stream are not wired. Delegates to ffmpeg-skill caption, probe.

## Steps

- [ ] **1. Single-source the version + dependencies**
  - Done when: contract version == pyproject
- [ ] **2. Align provides tool_id prefix** — `subtitle-skill/...` vs the agent's `subtitle/...`; fix or ADR
  - Done when: OS registry resolves without a mapping
- [x] **3. Locale profiles** — ja/zh/ko full-width counting, kinsoku line breaking, per-locale cps defaults ([#19](https://github.com/kajisho5/subtitle-skill/pull/19))
  - Done when: Japanese fixtures
- [ ] **4. Translation engine ADR** — the owner kept 字幕翻訳 here (2026-10-30); choose an offline-capable MT engine and check the licences of its code and weights
  - Done when: the ADR is merged
- [ ] **5. Machine translation** — engine chosen in the ADR, offline-capable, provenance records engine
  - Done when: ja<->en fixture
- [ ] **6. Multi-language mux** — several subtitle documents -> one mux
  - Done when: 2-language integration test

## Boundaries

Animated captions also exist in remotion (Captioned/Karaoke); transcription exports SRT/VTT as views. dubbing depends on translation.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
