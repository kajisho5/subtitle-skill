## v0.4.1 - 2026-10-31

- ci: check ffmpeg-skill v3.0.0 alongside main in the compatibility workflow (#26) (ded29c8)

## v0.4.0 - 2026-10-31

- feat(translate): Argos weight licence check; add opus-mt and ollama engines (#25) (58e4882)

## v0.3.0 - 2026-10-31

- feat: machine translation and multi-language mux (roadmap steps 5 and 6) (#24) (69159a2)

## v0.2.4 - 2026-10-31

- docs: choose the subtitle translation engine (ADR-004, roadmap step 4) (#23) (4b9c1b9)

## v0.2.3 - 2026-10-31

- fix: single-source the version and the ffmpeg-skill dependency (roadmap step 1) (#22) (a60c26e)

## v0.2.2 - 2026-10-30

- ci: stop inferring major from PR titles; pin third-party actions by SHA (#21) (1606655)

## v0.2.1 - 2026-10-30

- fix: publish provides tool ids as subtitle/... (ADR-002) (#20) (047d570)

## v0.2.0 - 2026-10-30

- feat: ja/zh/ko locale profiles (width counting, kinsoku, per-locale defaults) (#19) (10020b4)

## v0.1.5 - 2026-10-30

- fix: report the release version, single-sourced from pyproject.toml (#18) (b02325b)

## v0.1.4 - 2026-10-30

- docs: subtitle translation is owned here (owner decision) (#17) (c506d55)

## v0.1.3 - 2026-10-30

- Add ECOSYSTEM_ROADMAP.md: gaps, wiring and PR-sized steps from the 2026-10-30 ecosystem audit (#16) (ace672c)

## v0.1.2 - 2026-10-25

- Add .github/FUNDING.yml (#15) (b8f3893)

## v0.1.1 - 2026-10-23

- ci: bump GitHub Actions (checkout v7, setup-python v7, codeql-action v4, github-script v9) (#14) (ebf9271)

# Changelog

## v0.1.0 - 2026-10-23

- ci: make release.yml parse (a comment held an empty ${{ }} expression) (#13) (51f034b)
- Add release automation, PR autolabeling, CodeQL, Dependabot, SECURITY.md (#7) (72f3d2e)
- ci: run the Python 3.9 jobs on ubuntu-24.04 (#10) (fcf491d)
- docs(readme): fresnel-style header with a Liquid Glass app icon (#9) (a950b38)
- fix: work with ffmpeg-skill 2.x; check compatibility instead of byte drift (#8) (d1a96ac)
- Wire SubtitleStyle.color/bold/size into caption.py burn (--color/--bold/--size) (#6) (608fa9d)
- Re-vendor ffmpeg-skill fixture to 0.12.2; expose --mode mux and --audio-stream on render (#4) (c061941)
- Fix engine_version null compatibility break with video-production-agent (d1b9773)
- Add ffmpeg-skill vendor drift detection; re-vendor _contract.py (0.9.1 -> 0.9.2) (6a83185)
- Add subtitle-skill install: Agent Skill discovery, matching ffmpeg-skill's flag convention (a6c4880)
- Add CLAUDE.md durable state, complete PyPI-ready package metadata (b2f6e33)
- Add provides: publish Capability ids for cross-repository discovery (29fe6d5)
- Add MIT LICENSE, matching ffmpeg-skill (264ee99)
- README: add a Support (GitHub Sponsors) section (8fa7583)
- README: add project logo to the hero (729031d)
- README: OSS landing page matching ffmpeg-skill's redesign as ecosystem reference (814dcc4)
- Restructure README as an OSS landing page (6749bdd)
- Implement subtitle-skill: typed validation, SRT/WebVTT generation, burn-in delegation (#1) (75b822c)
- Initial commit (2c5a1fc)

