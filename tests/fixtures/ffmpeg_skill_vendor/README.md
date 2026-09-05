# Vendored ffmpeg-skill scripts (test fixture only)

`scripts/_common.py`, `scripts/_contract.py`, `scripts/caption.py` and
`scripts/probe.py` in this directory are a pinned, verbatim copy of the
corresponding files from
[kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) at commit
`2abd89ce4cda31b70fb44dcf3ef225cdec92aada` (v0.9.1), used only so
`tests/test_engine_render.py` and `tests/test_doctor.py` can run a real
integration test against ffmpeg-skill's actual `caption`/`probe`/`_contract`
CLI contract, without this repository depending on a live checkout of
ffmpeg-skill or network access during CI.

This is a test fixture, not a runtime dependency: `src/subtitle_skill` never
imports anything from this directory. `subtitle_skill.engine` always locates
a *real* ffmpeg-skill install (via `SUBTITLE_SKILL_FFMPEG_SKILL_DIR` or the
standard `~/.claude` / `~/.cursor` / `~/.codex` / `./.claude` skills
directories) at run time; these tests simply point that environment variable
at this vendored copy to exercise the same code path deterministically.

Distributed under ffmpeg-skill's own MIT license (`LICENSE` in this
directory). If ffmpeg-skill's `caption`/`probe`/`_contract` contract changes,
re-vendor these files from the new commit and update the hash above.
