# ADR-014: The 1.0 Compatibility Promise

Status: Accepted (Velisa ADR-031 / D46; the owner approved the 1.0 transition)

## Context

Velisa ADR-031 sets what 1.0 means for a Skill. From 1.0.0, a change that `si contract-diff` calls breaking, to a part of the contract the promise covers, ships only in a major release. The promise covers the `provides[]` entries marked `STABLE`, the tools they map to, their request, response and error shapes, and the top-level contract fields that are not tied to a capability. An `EXPERIMENTAL` entry may still change in a minor release, with a CHANGELOG line. subtitle-skill is fifth in ADR-031's order.

ADR-031's readiness measurements for this repository, checked again on 2026-11-21:

- **Breaking changes:** none in the tag diffs or in the `contract:*` labels of the merged pull requests.
- **Open roadmap steps:** only step 14 (style presets). Its design (ADR-013 part 5) adds one optional `render` field, `style_preset`, so the step is additive and does not block 1.0.
- **Known defects:** the two in ADR-031's table are fixed:
  - #45: `karaoke` accepted overlapping cue ranges. Fixed in #52; such cues are now refused.
  - #37: the scheduled ffmpeg-skill compatibility check was red, because ffmpeg-skill 4.1 refuses `--max-lines` with `--mode mux`. Fixed in #51 (0.11.1).
- **Other open items:** the open Dependabot pull requests bump CI actions only. No open issue asks for a contract change.
- **Agent:** video-production-agent pins this contract (`integration/siblings.lock.json`), so the re-pin proves the release end to end.

The contract has a `lifecycle` field only on `provides[]` entries. This repository has four: `subtitle.generate`, `subtitle.render`, `subtitle.translate` and `subtitle.mux`. Several newer features are optional fields of `generate` and `render`, not entries of their own. A parameter has no lifecycle field, so ADR-031 section 3 records a parameter's status in text: the README, the CHANGELOG and this ADR.

## Decision

### 1. `STABLE` entries

- **`subtitle.generate`** (`subtitle/generate`). It covers:
  - validation of the typed `SubtitleDocument`: the cue fields `id`, `start`, `end`, `text`, `speaker`, `style` (except `style.vertical`) and `metadata`;
  - the `constraints`, the ja/zh/ko locale profiles (ADR-001), and the fatal-or-observation split;
  - output as SRT, WebVTT, TTML (IMSC 1.1 Text Profile) and EBU-TT-D (ADR-007), with the response's `conformance`.

  Validation and SRT/WebVTT output have been in every release since 0.1.0. TTML and EBU-TT-D (0.6.0) follow published W3C and EBU specifications. Their output is checked against each profile's structural rules, and the tests also check it against the W3C TTML2 schema.
- **`subtitle.render`** (`subtitle/render`). It covers:
  - `mode` `burn` and `mux`, `audio_stream`, and the `style.color` / `bold` / `size` forwarding;
  - the delegation to ffmpeg-skill `caption` and `probe`, and the checks on the output;
  - `placement` (ADR-006, 0.5.0): faces from detection-skill `detections@1`, an optional `reframe@1`, and caller-named `avoid` regions.

  Burn and mux are the original operation of this skill (0.1.0), and video-production-agent's `subtitle_burn_in` calls them. `placement` takes its input as documents (face boxes) and runs no model here. Its response shape has not changed since 0.5.0.

A breaking change to anything listed above, or to the error codes and the top-level contract fields, ships only in a major release (2.0.0).

### 2. Experimental fields of the `STABLE` tools

These fields of `generate` and `render` stay experimental. Each may still change in a minor release, with a CHANGELOG line:

- **The ADR-013 fields:** `cuts`, `spoken_numbers` (in `generate` and `render`), `emphasis` and `legibility`. They were added in 0.11.0, the release before 1.0, and have had no field use yet. `spoken_numbers` has one locale (`en`), and others are still to be written. `legibility` thresholds come from measurements on synthetic frames only.
- **`animate` and `karaoke`** (ADR-011, 0.10.0). They pass straight through to ffmpeg-skill `caption`, which draws them. Their behaviour follows that engine: ADR-011 records caption.py versions in which `karaoke: "word"` with an entrance blinks out. #45 also changed what `karaoke` accepts. The cue `words` field, which `karaoke`, `shorts` and `cuts` read, is experimental for the same reason.
- **The other newer opt-in fields:**
  - `caption_profile: "shorts"` (ADR-009, 0.8.0);
  - `fonts_dir` (ADR-010, 0.9.0);
  - `regional_profile`, cue `ruby` and `style.vertical` (ADR-008, 0.7.0).

  Each has been out for two weeks or less. Each is an opt-in convention that an additional profile or platform may still reshape. One example is the shorts safe zone, which is the zone the platforms share today.

A request with none of these fields gets the `STABLE` behaviour of section 1. These fields are left out of the promise because they are newer or depend on another engine, not because they are known to be wrong.

### 3. `EXPERIMENTAL` entries

- **`subtitle.translate`.** Four of its five engines run a third-party model or service: Argos Translate (whose ja→en weight licence is unverified, ADR-004), OPUS-MT, a local Ollama LLM, and the DeepL API. Their output and their failure modes depend on model versions this repository does not control. Only the `memory` engine is deterministic, and that is not enough to stand behind the operation.
- **`subtitle.mux`** (multi-language mux, ADR-005). It depends on ffmpeg-skill 1.16+ for several tracks. It also depends on how caption.py treats the first track. That broke with ffmpeg-skill 4.1 (#37) and was fixed only in 0.11.1, one release before 1.0. It is promoted once the scheduled compatibility check has stayed green across ffmpeg-skill releases. Single-track soft subtitles are covered by `render` `mode: "mux"` (section 1).

### 4. How the promise is kept

- Promoting an entry (or a field of section 2) to `STABLE` is metadata only. si's `lifecycle-promoted` rule classifies it as `none`, so it can ship in any release.
- **Deprecation** of a `STABLE` entry or field (ADR-031 section 3):
  1. mark it `DEPRECATED` in a minor release, naming its replacement in the README and the CHANGELOG;
  2. keep it working for at least one more minor release and 30 days;
  3. remove it only in the next major release.
- **Breaking-change review.** `major-label-check.yml` fails a `contract:breaking` pull request on 1.x without the `major` label. For a break that is only in an `EXPERIMENTAL` entry, ADR-031 section 5 lets the reviewer remove `contract:breaking` by hand. si reports a field of a `STABLE` tool as part of that tool. So a break in a section 2 field counts as a break of `render` or `generate` until that field has an entry of its own. The reviewer can remove the label only when every breaking item in the contract-diff comment is a field listed in section 2, and the pull request and CHANGELOG entry name it.

### 5. Release

The 1.0 pull request carries the `major` label. release-drafter then resolves 0.11.x → 1.0.0, and `release_gate.py` treats `major` as release-level. No workflow changes. The contract diff of the pull request is `none` (two `lifecycle-promoted` items). `contract_version` stays `1.0.0`: it is a separate axis from the release version.

## Consequences

- A caller that sends only the section 1 fields can float across 1.x minors.
- A caller that uses `translate`, multi-language `mux` or any section 2 field reads the CHANGELOG on each minor release.
- video-production-agent's re-pin sees a 0.x → 1.0.0 release with a `none` diff and widens its gate to `1.0.`.
- Style presets (roadmap step 14) can land in a 1.x minor release as an experimental field.
