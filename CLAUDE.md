# CLAUDE.md — repository state for future sessions

This file exists so a future session (human or agent) can pick up work
without replaying conversation history. It is maintained by whoever last
touched the repository as its "autonomous maintainer" — update it
whenever repository state materially changes (new capability, changed
integration, closed gap, new known limitation).

## What this repository is

`subtitle-skill`: a deterministic subtitle validation / generation /
burn-in-rendering **execution skill**. It makes no editorial decisions
about subtitle content (no transcription, no diarization, no wording,
no cue-splitting judgement) — see `README.md` and `SKILL.md` for the
full responsibility boundary and agent-facing usage guide. Do not
duplicate that content here; this file is status, not spec.

**Status: CURRENT, working, tested.** Not experimental, not a stub.

## Where this sits in the ecosystem

```
kajisho5/ai-video-production-os   <- the "OS" repo named in system-map style prompts
kajisho5/video-production-agent   <- the actual orchestrating agent (Python, has real code)
kajisho5/subtitle-skill           <- this repo
kajisho5/ffmpeg-skill             <- downstream dependency of this repo's `render` operation
```

**`kajisho5/ai-video-production-os` is a placeholder, not a real system.**
As of this writing its `main` (`e764520`) contains exactly one file:
`README.md` with a single line, `# video-production-ecosystem`. There is
no OS contract, no capability-discovery mechanism, no runtime, nothing
to integrate against. Any documentation or code that talks about "OS
integration" as if that OS already has a contract is fabricating
architecture that does not exist — **do not do that**. Treat OS
integration as VISION-stage until that repository actually contains
something.

**`kajisho5/video-production-agent` now DOES call this skill.** This
was NOT true earlier in this repo's history (an earlier version of this
file, written against `video-production-agent` commit `287b685`,
recorded "does not call this skill yet" — that is now stale and was
corrected here after re-reading the real source at commit `d8a6c83`).
Verified against actual source, not assumed:
- `src/video_agent/skills/registry.py` declares `subtitle_generation`
  and `subtitle_burn_in` `SkillSpec`s that target this skill.
- `src/video_agent/tools/subtitle/locate.py` finds an installed
  subtitle-skill (a checkout's `src/subtitle_skill` run via
  `python -m subtitle_skill`, or the `subtitle-skill` console script) —
  the agent never imports subtitle-skill as a library, only invokes it
  as a subprocess, matching this repo's CLI-first design.
- `src/video_agent/tools/subtitle/adapter.py`'s `SubtitleAdapter` runs
  it as exactly `["subtitle-skill", "run", "-", "--json"]` with the
  request JSON on stdin — the same invocation shape this repo's own
  README documents.
- `src/video_agent/tools/subtitle/contract_0.1.0.json` pins a snapshot
  of this repo's contract. Diffed byte-for-byte against a live
  `subtitle-skill contract --json` run in this session: **identical**.
  If a future contract change here breaks that byte-equality, that pin
  is the thing that will (rightly) fail on video-production-agent's
  side — bumping this repo's contract version is a breaking change for
  a real, verified consumer, not a hypothetical one.

**A real cross-repo compatibility bug was found and fixed this
session** (see engine.py's `UNKNOWN_ENGINE_VERSION`): the adapter's
`_check_response()` requires a render response's `engine_version` field
to be a non-empty string, and treats anything else (including a JSON
`null`) as `INVALID_RESULT` — a non-retryable failure — rather than a
render failure. `ffmpeg_skill_version()` used to return `None` (→ JSON
`null`) whenever the installed ffmpeg-skill had no readable
`package.json`, which is a legitimate state (e.g. a hand-built or
vendored install with no npm metadata). That combination meant a
perfectly successful render could be reported to video-production-agent
as an unretryable invalid result. Fixed by having
`ffmpeg_skill_version()` return the literal string `"unknown"` instead
of `None` in that case — `engine_version` is now guaranteed to always
be a truthy string on a render response. Covered by
`tests/test_engine_boundaries.py::test_engine_version_is_never_null_without_package_json`
and an added assertion in
`tests/test_engine_render.py::test_render_delegates_to_real_ffmpeg_skill_caption`.

**`kajisho5/ffmpeg-skill` is a real, verified downstream dependency.**
`render` delegates burn-in to its `caption` tool by invoking
`scripts/caption.py` directly (there is no single dispatch endpoint in
ffmpeg-skill — every tool is its own script). Verified against
`kajisho5/ffmpeg-skill` commit `b51dc5e` (package.json version `0.9.2`)
as of this writing: `caption.py`, `probe.py`, `_common.py` are
byte-identical to the vendored copy (see
`tests/fixtures/ffmpeg_skill_vendor/README.md`); `_contract.py` had
drifted (a `color.py --correct` capability addition, unrelated to
`caption`/`probe`) and was re-vendored. **This has already moved twice**
(`2abd89c` → `d27c776` → `b51dc5e`) since this repo's render integration
was first written — do not assume it is still byte-identical by the
time you read this either. Run `python3 scripts/check_vendor_drift.py`
(or check the weekly `vendor-drift.yml` workflow run) before trusting it
blindly.

## Capabilities (what this repo actually exposes)

Two operations, both real and tested — see `contract --json` as the
authoritative source, never re-describe this from memory:

| Operation | Formats | Depends on |
|---|---|---|
| `generate` | SRT, WebVTT | nothing external |
| `render` | SRT only | a reachable ffmpeg-skill install (`caption` + `probe` tools) |

Capability/error/format lists live in code (`src/subtitle_skill/contract.py`,
`src/subtitle_skill/errors.py`, `src/subtitle_skill/doctor.py`) and are
generated at runtime — this file must never hardcode a second copy of
that list that can drift from `contract --json`.

## Known gaps / next highest-value tasks (as of this writing)

Ordered by value, not urgency:

1. **PyPI publication itself** — metadata is ready (see below); nothing
   has actually been uploaded. Requires a human decision (account,
   namespace, when) — not something to do unilaterally.
2. **`vendor-drift.yml` is weekly, not on every ffmpeg-skill release** —
   a same-day re-vendor after a real ffmpeg-skill change to
   `caption.py`/`probe.py` still needs someone (or a session) to notice
   and act on the workflow's result; it does not open an issue or PR by
   itself yet.

### Done since the gaps above were first written

- **`video-production-agent` integration** — verified real and working
  (see above); the pinned contract snapshot matches this repo's contract
  byte-for-byte, and a real cross-repo `engine_version` compatibility
  bug found this session has been fixed. No further action needed here
  unless a future contract-version bump requires coordinating with that
  repo's pinned snapshot.
- **Agent Skill installer** (`subtitle-skill install [--claude|--cursor|--codex|--all|--project|--dir PATH] [--uninstall] [--json]`,
  `src/subtitle_skill/installer.py`) — places the packaged `SKILL.md` in
  the standard agent skill directories, mirroring ffmpeg-skill's
  `bin/install.js` flag convention. Deliberately option (a) from the
  original note below: it copies only `SKILL.md`, not a runtime — the
  `subtitle-skill` command still needs `pip install` on `PATH`
  separately, and the CLI says so. Verified against a real, non-editable
  `pip install` (not just an editable checkout) that the packaged
  `SKILL.md` (via `[tool.setuptools.package-data]`) actually ships and
  that `install`/`install --all`/`install --uninstall` all work.
  `tests/test_installer.py` also guards the packaged copy against
  drifting from the repo-root `SKILL.md`.
- **Vendored ffmpeg-skill drift detection**
  (`scripts/check_vendor_drift.py`, `.github/workflows/vendor-drift.yml`,
  weekly + manual `workflow_dispatch`) — clones current ffmpeg-skill main
  and diffs `caption.py`/`probe.py`/`_common.py`/`_contract.py` against
  the vendored copy; separate from the main `ci.yml` so normal PRs never
  depend on that network fetch. Already caught one real drift
  (`_contract.py`, unrelated `color.py --correct` addition) the same
  session it was built, which was re-vendored immediately (see
  `tests/fixtures/ffmpeg_skill_vendor/README.md` for the commit history).

## Things intentionally NOT done, and why

- **No CI job clones ffmpeg-skill main for a live integration test in
  `ci.yml`** — would make this repo's normal-PR CI depend on the
  availability and stability of another repository's network fetch; the
  vendored copy plus the separate, non-blocking `vendor-drift.yml`
  schedule (see above) is the deliberate tradeoff.
- **No MCP server, plugin loader, or "OS SDK" exists here** — none of
  those exist in the actual OS repo either (see above); building one
  speculatively would be inventing architecture ahead of the thing it's
  supposed to integrate with.
- **No PyPI publication** — `pyproject.toml` now carries full PEP 621
  metadata (license, authors, classifiers, urls) so it's publish-ready,
  but nothing has actually been published; the README says so
  explicitly and that must stay true until it happens.

## Test / CI state (verify, don't trust this number blindly)

At last update: 97 tests, `pytest -q`, all passing; CI green on
Ubuntu/macOS/Windows × Python 3.9/3.11 (6 jobs, `.github/workflows/ci.yml`).
Re-run `pytest -q` yourself before relying on this — it is a snapshot,
not a promise.
