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
`render` delegates burn-in (or mux) to its `caption` tool by invoking
`scripts/caption.py` directly (there is no single dispatch endpoint in
ffmpeg-skill — every tool is its own script). Verified against
`kajisho5/ffmpeg-skill` commit `336e0c4d6311d2407daaa529ad71fee641f59b37`
(package.json version `0.12.2`) as of this writing, re-vendored to close
[subtitle-skill#3](https://github.com/kajisho5/subtitle-skill/issues/3):
`probe.py` is still byte-identical to the previous pin, but `caption.py`
and `_common.py` — the files `subtitle_skill.engine` actually invokes —
are NOT: `caption.py` gained `--mode mux`, `--audio-stream N`, and
SMPTE timecode support for `--text` cues since the last pin (`b51dc5e`,
v0.9.2); `_common.py` gained the shared helpers those need plus
unrelated stream-preservation fixes. **This jump skipped three
intermediate feature releases** (0.10.0-0.12.1) that went unnoticed
between sessions — the exact failure mode the old text of this section
predicted ("vendor-drift.yml is weekly... does not open an issue or PR
by itself yet", still true, see Known gaps below). Pin history:
`2abd89c` (v0.9.1) → `b51dc5e` (v0.9.2, `_contract.py`-only drift) →
`336e0c4d` (v0.12.2, `caption.py`+`_common.py` drift, this session). Do
not assume it is still byte-identical by the time you read this either
— run `python3 scripts/check_vendor_drift.py` (or check the daily
`vendor-drift.yml` workflow run) before trusting it blindly.

**`render` now exposes `caption.py`'s mux/audio-track capabilities
instead of hardcoding burn-only** (subtitle-skill#3 fix B, same session
as the re-vendor above — the re-vendor is what made these reachable to
test against in the first place). `engine.burn_in()` no longer builds a
fixed 4-token argv:
- `mode` (request field, default `"burn"`; validated against
  `engine.ALLOWED_RENDER_MODES` before ever reaching caption.py, since
  an invalid `--mode` choice there exits non-zero with a plain-text
  argparse error and no JSON at all) — `"mux"` adds `--mode mux`,
  producing a soft/toggleable subtitle track instead of burned-in
  pixels.
- `audio_stream` (request field, optional 0-based int) — threaded as
  `--audio-stream N` for either mode; type-checked here, range-checked
  by caption.py itself against the real input (mapped to
  `INVALID_INPUT`).
- `SubtitleDocument.language` — required and BCP47-validated by
  `models.py` since before this session, but never read by anything
  downstream until now — is forwarded as `--language` (mux-only;
  caption.py's own `--language` also feeds `--transcribe`, which
  subtitle-skill never uses). **Real caveat found by actually running
  it, not assumed:** `.mp4`/`.mov` output's `mov_text` mux codec
  silently drops a language tag that is not a 3-letter ISO 639-2 code —
  a plain 2-letter BCP47 tag like `"ja"`/`"en"` (exactly what
  `SubtitleDocument.language` validates and what a real caller sends)
  can vanish from that container's stream tags with ffmpeg still
  exiting 0. `.mkv`/`.webm` output writes the same value verbatim. See
  `engine.burn_in`'s docstring and README "ffmpeg-skill integration" for
  the full citation; `tests/test_engine_render.py`'s mux tests exercise
  this against the real (now-current) vendored `caption.py`, and the
  language-tag test deliberately uses `.mkv` output to isolate
  subtitle-skill's own forwarding logic from that muxer quirk.

**`render` now forwards a subset of `SubtitleStyle` to caption.py**
(subtitle-skill#5, this session). Previously `SKILL.md`/README documented
a deliberate full opt-out ("style is not forwarded to ffmpeg-skill's
caption tool during render... there is no lossless translation"). That
was overly broad: `color`/`bold`/`size` genuinely DO have a real
caption.py target (`--color`/`--bold`/`--size`); only `align`/`position`/
`line`/`italic` don't. Verified against caption.py's real argparse
definitions and force_style construction, not assumed:
- `color` -> `--color` verbatim (caption.py's own `color_hex()` validates).
- `bold` -> `--bold` when `True` (`action="store_true"`, no `--no-bold`).
- `size` (0..100 percent) -> `--size <round(size/100*288)>`. caption.py's
  own help text says `--size` is "ASS points relative to a 288p script
  height, scales automatically" -- confirmed empirically this session by
  burning the same `--size` into two real videos of different heights and
  measuring the rendered glyph's pixel height in each (it scaled
  proportionally with the real video height), not trusted from the help
  text alone.
- `align`/`position`/`line`/`italic` remain genuinely unmapped and
  undocumented as forwarded: no `--align` flag exists at all;
  `--position`'s 7 named anchors conflate horizontal justification with
  vertical anchor (its `"center"` is screen-center, not
  bottom-center-justified) so there is no lossless bucket for `align`, and
  no numeric/percent placement exists at all for `position`/`line`;
  `italic` has no force_style key in caption.py's plain-SRT path
  (though it already renders correctly per-cue via the pre-existing
  `<i>` SRT tag, unrelated to force_style).
- The real complication: `formats.generate_srt` already rejected
  `color`/`size` with `UNSUPPORTED_FORMAT` for *any* caller (it's meant
  for a bare .srt file, which truly can't encode them) -- `render`'s own
  SRT generation would have hit that same rejection before ever reaching
  caption.py, making the new wiring dead code. Fixed by giving
  `generate_srt` an internal `allow_style_fields` parameter that
  `operations._run_render` sets to `{"color", "size"}` only for
  `mode="burn"` (never for `generate`, never for `mode="mux"`, and never
  including `align`/`position`/`line`).
- caption.py's force_style is a single, whole-burn setting with no
  per-cue equivalent, but `SubtitleStyle` is per-cue on this model.
  `operations._run_render` reduces a document's cue styles to the one
  `color`/`bold`/`size` triple actually forwarded, requiring every cue
  that sets a given field to agree; a genuine conflict is `INVALID_INPUT`,
  never resolved by silently picking one cue's value.
- Covered by `tests/test_engine_style.py` (argv construction against a
  fake caption.py, including the "unset style changes nothing" regression
  guard), new cases in `tests/test_formats.py` (the `allow_style_fields`
  escape hatch), and new cases in `tests/test_engine_render.py` against
  the real vendored caption.py (reading the persisted sidecar's
  `engine_response.commands` for the real, caption.py-reported
  `force_style` string -- `execute()`'s own response never carries it).

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
2. **Compatibility with new ffmpeg-skill releases is checked daily, not
   fixed automatically** — `vendor-drift.yml` now runs the whole test suite
   against ffmpeg-skill main every day and fails only when subtitle-skill
   actually breaks (a plain new release passes). A real break still needs
   someone to fix `subtitle_skill.engine`; the workflow does not open an
   issue or PR by itself.
3. **No SMPTE timecode cue support in `render`** — ffmpeg-skill 0.12.x's
   `caption.py --text` accepts `hh:mm:ss:ff` SMPTE non-drop-frame
   timecode cues (`parse_time(fps=...)`/`fmt_smpte_time()`, closing
   ffmpeg-skill#54) when `--fps` (or the input's own fps) is known;
   `SubtitleCue.start`/`end` here are decimal seconds only, with no
   analog. Broadcast/EDL callers must do their own frame-to-seconds math
   before building a `SubtitleDocument`. Deliberately out of scope for
   subtitle-skill#3 (fix C in that issue is the cross-repo
   `subtitle.burn`-vs-`subtitle.render` ownership question, not this);
   noted here as the next real capability gap once someone decides this
   skill's typed cue model should grow a `fps` concept.
4. **`SubtitleStyle.align`/`.position`/`.line`/`.italic` still have no
   `render`-burn representation** — not an oversight, a verified real gap
   (see the subtitle-skill#5 entry below and README's "SubtitleStyle →
   caption.py" table for the exact per-field reasoning): caption.py has no
   `--align` flag and no numeric/percent placement at all, and its
   plain-SRT force_style path never exposes an `Italic=` key. Closing this
   would require either a caption.py feature that does not exist today, or
   moving subtitle-skill's own render path onto `--ass` (a bigger, riskier
   change than force_style flags, and this skill never sends `--ass`
   today) — not something to do speculatively.

### Done since the gaps above were first written

- **`render` forwards `SubtitleStyle.color`/`.bold`/`.size` to
  caption.py's `--color`/`--bold`/`--size`, for `mode="burn"`**
  (subtitle-skill#5, this session) — see the dedicated entry above (right
  after the ffmpeg-skill dependency section) for the exact mapping, the
  `size` percent-to-288-points conversion and its empirical verification,
  the `formats.generate_srt` `allow_style_fields` fix that made forwarding
  `color`/`size` actually reachable, and the per-cue-vs-whole-burn
  reduction `operations._run_render` performs. `align`/`position`/`line`/
  `italic` remain a documented, verified gap (see above), not silently
  dropped or guessed at.
- **`render` exposes ffmpeg-skill/caption's mux mode, audio-track
  selection, and language tagging** (subtitle-skill#3 fix B) — `mode:
  "mux"` (soft/toggleable subtitle track, vs. the default `"burn"`),
  `audio_stream` (0-based multi-track selection), and
  `SubtitleDocument.language` forwarded as the mux language tag (mux
  only; previously validated but never read anywhere). See the
  ffmpeg-skill dependency section above for the real MOV/MP4
  language-tag caveat this surfaced. Covered by new tests in
  `tests/test_engine_render.py` against the re-vendored real
  `caption.py`.

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
- **ffmpeg-skill compatibility check**
  (`scripts/check_vendor_drift.py`, `.github/workflows/vendor-drift.yml`,
  daily + manual `workflow_dispatch`) — clones current ffmpeg-skill main
  and runs the full test suite with `SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC`
  pointed at it, so the render/doctor tests drive the real current
  `caption.py`/`probe.py`. It replaced a byte comparison against the
  vendored copy, which failed on every ffmpeg-skill release (several a
  week) whether or not anything broke. Its first run against 2.5.1 found
  two real breaks, both fixed in `engine.py`: ffmpeg-skill 2.0 moved
  `_common.py` into a `_common/` package (subtitle-skill no longer found
  the install at all), and 1.10 refuses to replace an existing output
  without `--overwrite` (a re-render failed). The vendored copy (0.12.2)
  stays as the offline fixture for `ci.yml`; re-vendoring it is optional.

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

At last update: 148 tests, `pytest -q`, all passing; CI green on
Ubuntu/macOS/Windows × Python 3.9/3.11 (6 jobs, `.github/workflows/ci.yml`).
Re-run `pytest -q` yourself before relying on this — it is a snapshot,
not a promise.
