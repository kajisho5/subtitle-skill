# Vendored ffmpeg-skill scripts (test fixture only)

The files under `scripts/` are a pinned, verbatim copy of the corresponding
files from [kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill)
at commit `86b5145c86824309cb8cadea9739e217580204bc` (package.json version
`2.24.0`): `caption.py`, `probe.py`, `_contract.py`, the `_common/` package,
and `_platforms.py` / `_ass_overlay.py` (which `caption.py` imports). They
are used only so `tests/test_engine_render.py`, `tests/test_mux.py` and
`tests/test_doctor.py` can run real integration tests against ffmpeg-skill's
actual `caption`/`probe`/`_contract` CLI, without this repository depending
on a live checkout of ffmpeg-skill or network access during CI.

`scripts/check_vendor_drift.py` (repo root) runs the same tests against
ffmpeg-skill's current main every day (see
`.github/workflows/vendor-drift.yml`), so a real upstream break becomes
visible while an ordinary new release does not fail anything. This copy does
not have to match main byte for byte; re-vendor it only when a test needs
behaviour this pin does not have.

Pin history: `2abd89c` (v0.9.1) → `b51dc5e` (v0.9.2, `_contract.py`-only
drift) → `336e0c4d` (v0.12.2, re-vendored to close
[subtitle-skill#3](https://github.com/kajisho5/subtitle-skill/issues/3):
`--mode mux`, `--audio-stream`) → `86b5145c` (v2.24.0, re-vendored for
`ECOSYSTEM_ROADMAP.md` step 6: the multi-language mux needs `caption.py`'s
repeatable `--srt FILE:LANG`, `--track-title` and `--default-track`, added in
1.16.0). ffmpeg-skill 2.0 moved `_common.py` into a `_common/` package, so the
old single `_common.py` is gone from this copy; `subtitle_skill.engine`
already handled both layouts (found by the daily drift check). Before
re-vendoring, the whole suite was run against this exact checkout with
`SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC` and passed unchanged.

This is a test fixture, not a runtime dependency: `src/subtitle_skill` never
imports anything from this directory. `subtitle_skill.engine` always locates
a *real* ffmpeg-skill install (via `SUBTITLE_SKILL_FFMPEG_SKILL_DIR` or the
standard `~/.claude` / `~/.cursor` / `~/.codex` / `./.claude` skills
directories) at run time; these tests simply point that environment variable
at this vendored copy to exercise the same code path deterministically.

Distributed under ffmpeg-skill's own MIT license (`LICENSE` in this
directory). If ffmpeg-skill's `caption`/`probe`/`_contract` contract changes,
re-vendor these files from the new commit and update the hash above.
