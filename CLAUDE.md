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

**`kajisho5/video-production-agent` is real but does not call this
skill yet.** Verified by reading its source (not assumed) as of commit
`287b685`: `src/video_agent/skills/registry.py`'s `caption_generation`
`SkillSpec` names `ffmpeg-skill/caption` as its tool candidate directly,
requires capability `asr:whisper`, and is explicitly commented
`# declared, not implemented in Phase 1` (i.e. Phase 3, unimplemented).
There is zero reference to `subtitle-skill` or `SubtitleDocument`
anywhere in that repository. This is not a bug in either repo — it's
simply that the integration hasn't been built yet. **subtitle-skill
cannot fix this from its own side**; the fix is in
`video-production-agent`'s `registry.py` (out of this repo's authority —
do not attempt it from here).

**`kajisho5/ffmpeg-skill` is a real, verified downstream dependency.**
`render` delegates burn-in to its `caption` tool by invoking
`scripts/caption.py` directly (there is no single dispatch endpoint in
ffmpeg-skill — every tool is its own script). Verified against
`kajisho5/ffmpeg-skill` commit `2abd89c` (skill version 0.9.1); ffmpeg-skill's
main has since moved to `d27c776` (README redesign only, per that
commit's title) — re-verify `scripts/caption.py` / `scripts/probe.py`
/ `scripts/_common.py` against current main before trusting this
integration blindly in a future session; don't assume it's still
byte-identical to the vendored test copy forever.

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

1. **Vendored ffmpeg-skill test fixture can silently drift.**
   `tests/fixtures/ffmpeg_skill_vendor/` is a pinned, byte-for-byte copy
   of ffmpeg-skill's `caption.py`/`probe.py`/`_common.py`/`_contract.py`
   at commit `2abd89c`. Nothing detects if real ffmpeg-skill main moves
   on and this copy quietly stops matching it. A periodic CI job (or a
   pre-release check) that clones current ffmpeg-skill main and diffs
   those four files against the vendored copy would catch that; not yet
   built.
2. **`video-production-agent` integration is unstarted** (see above) —
   not this repo's task to fix, but worth re-checking periodically
   whether that repo has moved past its Phase 1 and needs a
   `SubtitleDocument`-shaped `render`/`generate` call added to its
   registry.
3. **PyPI publication itself** — metadata is ready (see below); nothing
   has actually been uploaded. Requires a human decision (account,
   namespace, when) — not something to do unilaterally.

### Done since the gaps above were first written

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

## Things intentionally NOT done, and why

- **No CI job clones ffmpeg-skill main for a live integration test** —
  would make this repo's CI depend on the availability and stability of
  another repository's network fetch; the vendored copy (gap #2 above)
  is the deliberate tradeoff.
- **No MCP server, plugin loader, or "OS SDK" exists here** — none of
  those exist in the actual OS repo either (see above); building one
  speculatively would be inventing architecture ahead of the thing it's
  supposed to integrate with.
- **No PyPI publication** — `pyproject.toml` now carries full PEP 621
  metadata (license, authors, classifiers, urls) so it's publish-ready,
  but nothing has actually been published; the README says so
  explicitly and that must stay true until it happens.

## Test / CI state (verify, don't trust this number blindly)

At last update: 96 tests, `pytest -q`, all passing; CI green on
Ubuntu/macOS/Windows × Python 3.9/3.11 (6 jobs, `.github/workflows/ci.yml`).
Re-run `pytest -q` yourself before relying on this — it is a snapshot,
not a promise.
