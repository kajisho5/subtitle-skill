# Vendored ffmpeg-skill scripts (test fixture only)

`scripts/_common.py`, `scripts/_contract.py`, `scripts/caption.py` and
`scripts/probe.py` in this directory are a pinned, verbatim copy of the
corresponding files from
[kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) at commit
`b51dc5e8c846e08025f713f5cb57cfff21de3b39` (package.json version
`0.9.2`), used only so `tests/test_engine_render.py` and
`tests/test_doctor.py` can run a real integration test against
ffmpeg-skill's actual `caption`/`probe`/`_contract` CLI contract, without
this repository depending on a live checkout of ffmpeg-skill or network
access during CI.

`scripts/check_vendor_drift.py` (repo root) checks these four files
against ffmpeg-skill's current main on a schedule (see
`.github/workflows/vendor-drift.yml`) so a real upstream change becomes
visible instead of silently making these tests exercise a contract
ffmpeg-skill no longer has. Previously vendored from commit `2abd89c`
(v0.9.1); re-vendored here after that check found `_contract.py` had
changed upstream (a new `color.py --correct` capability entry) --
`caption.py`, `probe.py`, and `_common.py`, the files subtitle-skill's
`render` actually invokes, were still byte-identical despite the
package version bump to 0.9.2, which is itself a real-world
confirmation of why `subtitle_skill.engine`'s render identity hashes
script *content*, not ffmpeg-skill's self-reported version string (see
README "Deterministic identity and reuse").

This is a test fixture, not a runtime dependency: `src/subtitle_skill` never
imports anything from this directory. `subtitle_skill.engine` always locates
a *real* ffmpeg-skill install (via `SUBTITLE_SKILL_FFMPEG_SKILL_DIR` or the
standard `~/.claude` / `~/.cursor` / `~/.codex` / `./.claude` skills
directories) at run time; these tests simply point that environment variable
at this vendored copy to exercise the same code path deterministically.

Distributed under ffmpeg-skill's own MIT license (`LICENSE` in this
directory). If ffmpeg-skill's `caption`/`probe`/`_contract` contract changes,
re-vendor these files from the new commit and update the hash above.
