# Vendored ffmpeg-skill scripts (test fixture only)

`scripts/_common.py`, `scripts/_contract.py`, `scripts/caption.py` and
`scripts/probe.py` in this directory are a pinned, verbatim copy of the
corresponding files from
[kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) at commit
`336e0c4d6311d2407daaa529ad71fee641f59b37` (package.json version
`0.12.2`), used only so `tests/test_engine_render.py` and
`tests/test_doctor.py` can run a real integration test against
ffmpeg-skill's actual `caption`/`probe`/`_contract` CLI contract, without
this repository depending on a live checkout of ffmpeg-skill or network
access during CI.

`scripts/check_vendor_drift.py` (repo root) runs the same tests against
ffmpeg-skill's current main every day (see
`.github/workflows/vendor-drift.yml`), so a real upstream break becomes
visible while an ordinary new release does not fail anything. This copy no
longer has to match main byte for byte; re-vendor it only when a test needs
behaviour this pin does not have. Pin history: `2abd89c` (v0.9.1) →
`b51dc5e` (v0.9.2, `_contract.py`-only drift, a `color.py --correct`
capability entry) → `336e0c4d` (v0.12.2, re-vendored here to close
[subtitle-skill#3](https://github.com/kajisho5/subtitle-skill/issues/3)).
This jump skips three intermediate feature releases (0.10.0-0.12.1) that
went unnoticed between sessions -- exactly the failure mode
`CLAUDE.md`'s "vendor-drift.yml is weekly... does not open an issue or
PR by itself yet" gap predicted. This time `caption.py` and
`_common.py` -- the files `subtitle_skill.engine` actually invokes --
are NOT byte-identical to the previous pin: `caption.py` gained
`--mode mux` (soft/toggleable subtitle track), `--audio-stream N`, and
SMPTE timecode support for `--text` cues; `_common.py` gained the
shared helpers those need (`parse_time(fps=...)`, `fmt_smpte_time()`,
richer `probe()` stream detail) plus unrelated stream-preservation
fixes from 0.12.0/0.12.1. `subtitle_skill.engine.burn_in()` was updated
in the same change to pass `--mode`/`--audio-stream`/`--language`
through to `caption.py` (see README "ffmpeg-skill integration") --
`probe.py` remains byte-identical across all three pins.

This is a test fixture, not a runtime dependency: `src/subtitle_skill` never
imports anything from this directory. `subtitle_skill.engine` always locates
a *real* ffmpeg-skill install (via `SUBTITLE_SKILL_FFMPEG_SKILL_DIR` or the
standard `~/.claude` / `~/.cursor` / `~/.codex` / `./.claude` skills
directories) at run time; these tests simply point that environment variable
at this vendored copy to exercise the same code path deterministically.

Distributed under ffmpeg-skill's own MIT license (`LICENSE` in this
directory). If ffmpeg-skill's `caption`/`probe`/`_contract` contract changes,
re-vendor these files from the new commit and update the hash above.
