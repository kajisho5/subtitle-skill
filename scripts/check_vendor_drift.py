#!/usr/bin/env python3
"""Check that subtitle-skill still works with ffmpeg-skill's current main
(or a given branch / tag: `--ref v3.0.0`).

Clones ffmpeg-skill main (or `--ref`) and runs this repository's whole test suite with
SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC pointed at it, so the render and
doctor tests drive the real, current caption.py / probe.py instead of the
pinned copy in tests/fixtures/ffmpeg_skill_vendor/.

A new ffmpeg-skill release is not drift by itself: ffmpeg-skill ships
several releases a week, and a byte comparison against the pinned copy
failed on every one of them. This fails only when subtitle-skill stops
working against the current release.

This is NOT part of the main test suite / CI job (see CLAUDE.md
"Things intentionally NOT done, and why"): the main suite deliberately
does not depend on network access to another repository. It runs on the
`.github/workflows/vendor-drift.yml` schedule, or by hand:

    python3 scripts/check_vendor_drift.py
    python3 scripts/check_vendor_drift.py --ref v3.0.0
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/kajisho5/ffmpeg-skill"
ROOT = Path(__file__).resolve().parent.parent


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default=None, help="ffmpeg-skill branch or tag to check (default: main)")
    args = ap.parse_args(argv)
    ref = args.ref or "main"
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str) / "ffmpeg-skill"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1"] + (["--branch", args.ref] if args.ref else []) + [REPO_URL, str(tmp)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"could not clone {REPO_URL}: {exc.stderr}", file=sys.stderr)
            return 2

        head = subprocess.run(
            ["git", "-C", str(tmp), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        try:
            version = json.loads((tmp / "package.json").read_text(encoding="utf-8")).get("version", "?")
        except (OSError, ValueError):
            version = "?"
        print(f"ffmpeg-skill {ref} is at {head} (version {version})")

        env = dict(os.environ, SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC=str(tmp))
        result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=env)
        if result.returncode != 0:
            print(
                f"\nINCOMPATIBLE: the test suite fails against ffmpeg-skill {version} ({head}). "
                "Fix subtitle_skill.engine for the new caption/probe behaviour; re-vendoring "
                "tests/fixtures/ffmpeg_skill_vendor/ is optional."
            )
            return 1

        print(f"\nCompatible: the test suite passes against ffmpeg-skill {version}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
