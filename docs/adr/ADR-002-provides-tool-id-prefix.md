# ADR-002: `provides` Tool Ids Use the Short `subtitle/` Prefix

Status: Accepted

## Context

`contract --json` publishes `provides`, one entry per operation. Before this change each entry's `tool_id` was built from `SKILL_ID`, so it read `subtitle-skill/generate` and `subtitle-skill/render`. video-production-agent registers this Skill as the package `subtitle` and calls its tools `subtitle/generate` and `subtitle/render` (`src/video_agent/tools/subtitle/adapter.py`, `TOOL_GENERATE` / `TOOL_RENDER`). The two names did not match.

`ECOSYSTEM_ROADMAP.md` step 2 asks for the prefixes to be aligned, or for an ADR saying why not. It is done when the OS registry resolves this Skill without a mapping.

What was found, read on 2026-10-30:

1. **There is one rule across the ecosystem, and this repository broke it: the `tool_id` prefix is the id the agent registers the Skill under.** That id is not always the `skill_id`:

   | Skill | `skill_id` | `provides[].tool_id` | Agent package id |
   |---|---|---|---|
   | thumbnail-skill | `thumbnail-skill` | `thumbnail/render`, `thumbnail/extract_frame` | `thumbnail` |
   | transcription-skill | `transcription-skill` | `transcription/transcribe` | `transcription` |
   | qc-skill, ingest-skill, color-grading-skill, motion-graphics-skill, audio-production-skill | the short name (`qc`, `ingest`, ...) | `qc/run`, `ingest/offload`, ... | the same short name |
| detection-skill, diarization-skill, slide-sync-skill | the short name | `detection/faces`, ... | not wired in the agent yet |
   | ffmpeg-skill | `ffmpeg-skill` | `ffmpeg-skill/probe`, ... | `ffmpeg-skill` |
| blender-skill, imagemagick-skill, remotion-ffmpeg-skill | the long name | `blender-skill/...`, ... | not wired in the agent yet |
   | **subtitle-skill (before)** | `subtitle-skill` | **`subtitle-skill/generate`** | **`subtitle`** |

   The domain skills use the short name and the engine skills use the long name. In every wired row except this repository's, the agent's tool ids and the Skill's own tool ids have the same prefix. qc-skill's ids differ after the prefix (`qc/run` against the agent's `qc/check`); the OS decision log D9 records that as an intended difference in granularity, not a naming error. thumbnail-skill and transcription-skill are named like this repository (`*-skill`), and they already publish the short prefix.
2. **video-production-agent's `provides` diagnostic reports this Skill as `PROVIDES_MISMATCH` for both capabilities.** It compares tool ids as strings (`video_agent/skills/diagnostics.py`, `check_provides()`). The OS decision log (`AI-video-production-OS` `docs/ecosystem/DECISION_LOG.md` D9) found that this is a simple one-to-one naming difference. It did not fix it on the agent side, because the agent's `SKILL_ID = "subtitle"` is also its package registry key, its `CapabilityResolver` capability name and two `SkillSpec.required_capabilities` entries, and a rename there has to change all of them together.
3. **The OS registry (`registry/registry.py`) joins on the capability id (`subtitle.generate`), not on the tool id.** The schema (`registry/capability_contract.schema.json`, `ProvidesEntry.tool_id`) only requires a non-empty string and gives `qc/run` and `ffmpeg-skill/audio` as examples. So it allows both prefixes. The tool id is what a resolved capability hands to the caller. Before this change a caller had to map `subtitle-skill/...` to the agent's `subtitle/...`.
4. **No program uses the old ids.** Searching every sibling checkout found `subtitle-skill/generate` and `subtitle-skill/render` only in video-production-agent's pinned snapshot `contract_0.2.0.json`, one hand-written fixture in its `tests/test_skills_diagnostics.py`, and two OS documents that record the mismatch. No CLI, dispatcher or registry accepts a tool id as input. `subtitle-skill run` takes `"operation": "generate" | "render"`.

## Decision

1. **`provides[].tool_id` is `subtitle/<operation>`:** `subtitle/generate` and `subtitle/render`. The prefix is `contract.TOOL_ID_PREFIX = "subtitle"`. This repository changes, not the agent, because the agent's ids already follow the ecosystem rule, and changing them would need the coordinated rename described in point 2 of the context.
2. **`skill_id` stays `subtitle-skill`.** The agent's `check_contract()` and its response checks compare `skill_id` / `skill` with the exact string `subtitle-skill`, and the package name, console script and `doctor` / `run` output all use it. Only the tool ids use the short prefix, the same split thumbnail-skill and transcription-skill already make.
3. **The capability ids do not change** (`subtitle.generate`, `subtitle.render`), and neither does `lifecycle`.
4. **No alias for the old ids.** Nothing resolves or invokes them (point 4 of the context), so the contract does not list both. Listing both would make the registry see two tool ids for one capability. The old ids appear only in the history of this ADR.
5. **`contract_version` stays `1.0.0`.** Nothing a request or response carries changes. `operations`, `parameters`, `capabilities`, `errors` and `out_of_scope` are unchanged. `provides` is discovery metadata, which video-production-agent's contract checks do not read.

## Consequences

- The OS registry resolves `subtitle.generate` / `subtitle.render` to `subtitle-skill` with the tool ids `subtitle/generate` / `subtitle/render`. These are the agent's own tool ids, so no mapping is needed. This was checked by registering the new `contract --json` in `AI-video-production-OS`'s `CapabilityRegistry`.
- **Not drift for video-production-agent.** Its `DRIFT_KEYS` for this Skill (`skill_id`, `version`, `contract_version`, `deterministic`, `capabilities`, `operations`, `out_of_scope`, `parameters`, `errors`) do not include `provides`, and neither does `FATAL_DRIFT_KEYS`. Run against the new contract with `version` 0.2.0, `check_contract()`, `contract_drift()` and `contract_breaking_drift()` each return an empty list. The Skill stays available.
- Run against the new contract, the agent's `check_provides()` now reports `PROVIDES_VALID` for both capabilities instead of `PROVIDES_MISMATCH`.
- Follow-ups in video-production-agent, which this repository does not change:
  - re-pin `contract_0.2.0.json` from a release that contains this change, so its `provides` shows the new ids;
  - update or add a `tests/test_skills_diagnostics.py` case that expects `PROVIDES_VALID` for the new ids;
  - drop the adapter docstring line saying "the contract declares no tool ids".
  - None of these is needed for the agent to keep working.
- `tests/test_contract.py` pins the new ids, the unchanged `skill_id` and `contract_version`, and that every tool id names a real operation.
