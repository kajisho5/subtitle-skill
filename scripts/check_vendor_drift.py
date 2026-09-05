#!/usr/bin/env python3
"""Detect drift between the vendored ffmpeg-skill test fixture
(tests/fixtures/ffmpeg_skill_vendor/scripts/) and the real, current
ffmpeg-skill main.

This is NOT part of the main test suite / CI job (see CLAUDE.md
"Things intentionally NOT done, and why"): the main suite deliberately
does not depend on network access to another repository. This script is
run manually, or by the separate, non-blocking
`.github/workflows/vendor-drift.yml` schedule, precisely so a real
upstream change to the files subtitle_skill.engine actually invokes
(caption.py, probe.py, _common.py) or to the contract generator
(_contract.py) becomes visible instead of silently making the vendored
render tests test something ffmpeg-skill no longer does.

    python3 scripts/check_vendor_drift.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/kajisho5/ffmpeg-skill"
WATCHED_FILES = ["scripts/_common.py", "scripts/caption.py", "scripts/probe.py", "scripts/_contract.py"]
VENDOR_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ffmpeg_skill_vendor" / "scripts"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(tmp)],
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

        drifted = []
        for rel in WATCHED_FILES:
            name = Path(rel).name
            current = tmp / rel
            vendored = VENDOR_DIR / name
            if not current.exists():
                drifted.append((name, "removed upstream"))
                continue
            if not vendored.exists():
                drifted.append((name, "not vendored locally (new file to add)"))
                continue
            if current.read_bytes() != vendored.read_bytes():
                drifted.append((name, "content differs"))

        print(f"ffmpeg-skill main is at {head}")
        if drifted:
            print("DRIFT DETECTED:")
            for name, reason in drifted:
                print(f"  {name}: {reason}")
            print(
                "\nRe-vendor: copy the changed file(s) from ffmpeg-skill main into "
                "tests/fixtures/ffmpeg_skill_vendor/scripts/, update the pinned commit "
                "hash in tests/fixtures/ffmpeg_skill_vendor/README.md, run the test "
                "suite, and re-verify subtitle_skill.engine's caption.py/probe.py "
                "invocation still matches the new contract before merging."
            )
            return 1

        print("No drift: caption.py / probe.py / _common.py / _contract.py match ffmpeg-skill main.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
