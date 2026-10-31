# ADR-003: The Version Lives Only in `pyproject.toml`; the ffmpeg-skill Dependency Comes From One Constant

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 1 is "Single-source the version + dependencies". It is done when the contract version equals `pyproject.toml`.

What was found, read on 2026-10-31:

1. **The version was already read from `pyproject.toml` in a source checkout.** `subtitle_skill._version()` replaced a hard-coded `0.1.0` earlier, and `contract --json` reports `0.2.2` = `pyproject.toml`. The release workflow (`.github/workflows/release.yml`) bumps only `pyproject.toml`, with an inline `sed`.
2. **The installed-copy fallback was hand-rolled.** It globbed `subtitle_skill-*.dist-info/METADATA` over `sys.path`. That takes the first match in path order and, within one directory, in lexicographic order, so two leftover dist-info directories report the wrong one (`0.10.0` sorts before `0.9.0`).
3. **Nothing tested the rule.** No test compared the contract or doctor with `pyproject.toml`, and nothing checked that the release bump moves them.
4. **The contract published no `dependencies`.** The AI Video Production OS schema (`registry/capability_contract.schema.json`) has a `dependencies[]` field: `skill_id` plus a `version_range` of the other Skill's `contract_version`, a lower bound and never an exact pin. audio-production-skill, color-grading-skill and motion-graphics-skill already publish ffmpeg-skill there. This repository delegates `render` to ffmpeg-skill's `caption` and `probe`, but said so only in prose (`engine.py`'s docstring: verified against contract_version `"1.0"`) and in `operations.render.delegates_to`.

## Decision

- **One source for the version: `pyproject.toml`.** `_version()` follows the same rule, in the same shape, as qc-skill (its ADR-020) and motion-graphics-skill (its ADR-21):
  1. This checkout's `pyproject.toml`, used only if its `name` is `subtitle-skill`. This covers a source checkout, including an editable install, whose recorded metadata goes stale on the next bump.
  2. Otherwise `importlib.metadata.version("subtitle-skill")`, which reads the installed wheel's metadata, built from that same file.
  3. Otherwise `0.0.0+unknown`, never a plausible but wrong number.

  `__version__`, `SKILL_VERSION`, `contract --json`, `doctor --json` and provenance all use this value.
- **The release bump stays one write, and checks itself.** The inline `sed` moved into `scripts/bump_version.py`, which `release.yml` runs with `NEW_VERSION`. It still writes only `pyproject.toml`. Then it imports the package from `src/` and exits non-zero unless `__version__`, the contract's `version` and doctor's `version` all equal `NEW_VERSION`. That stops the release before anything is committed or tagged. This repository commits no contract snapshot, so nothing needs regenerating.
- **One source for the dependency: `engine.FFMPEG_SKILL_CONTRACT_VERSION = "1.0"`.** This is the ffmpeg-skill contract_version the delegating code was written against. `contract.dependencies()` derives this entry from it, together with `engine.FFMPEG_SKILL_TOOLS`:

  `{"skill_id": "ffmpeg-skill", "version_range": ">=1.0", "range_of": "contract_version", "required": false, "for": ["subtitle/render"], "tools": ["ffmpeg-skill/caption", "ffmpeg-skill/probe"]}`

  The range has a lower bound only, as the OS schema describes. Nothing compares it at run time: what a located checkout can do is still checked on that checkout (`engine.is_installed`, doctor's capability check). `required` is `false` because `generate` never touches ffmpeg-skill. `for` names the one tool that needs it.
- **Not chosen: have the release also bump a constant.** audio-production-skill's `bump_version_files.sh` writes both `pyproject.toml` and `VERSION = "..."`. Two copies stay correct only while a second regex keeps matching. One copy cannot drift.
- **Not chosen: an upper bound (`<2.0`).** Some sibling Skills publish one. The OS schema says Skills do not cap the upper version, and this Skill has no version window.

## Consequences

- `contract --json` gains exactly one top-level key, `dependencies`. Every other byte is unchanged: `version` (`0.2.2`), `contract_version` (`1.0.0`), operations, provides, parameters and errors are the same. It is an additive change, so `contract_version` stays `1.0.0`.
- **video-production-agent is not affected.** Its subtitle adapter compares `DRIFT_KEYS = (skill_id, version, contract_version, deterministic, capabilities, operations, out_of_scope, parameters, errors)`. `dependencies` is not among them, so it is neither drift nor fatal drift, and `check_contract()` does not read it. The agent gates on `SUPPORTED_SKILL_VERSIONS = ("0.1.", "0.2.")`, so this change ships as a patch release (`fix:`/`chore:` title, never `feat:`).
- `tests/test_version_source.py` guards all of this:
  - `__version__`, the contract and doctor (in-process and through the CLI) equal `pyproject.toml`.
  - `scripts/bump_version.py`, run on a copy of the checkout, moves all of them and refuses a non-`X.Y.Z` version.
  - A copy without `pyproject.toml` reads a dist-info `Version`, and reports `0.0.0+unknown` when there is none.
  - `dependencies` is the lower bound derived from the engine constant.
