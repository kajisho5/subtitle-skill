"""Release-workflow version bump: write NEW_VERSION into pyproject.toml, then prove it took effect.

pyproject.toml is the only file that carries this package's version
(docs/adr/ADR-003-single-source-version.md): `subtitle_skill.__version__`, the
contract's `version` and doctor's `version` are all read from it at runtime, so
this one write is the whole bump. After writing, the package is imported from
this checkout's src/ (never from an installed copy) and every reported version
must equal NEW_VERSION; otherwise the release stops here, before anything is
committed or tagged.

Usage (from the repository root): NEW_VERSION=X.Y.Z python3 scripts/bump_version.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_LINE_RE = re.compile(r'(?m)^version = "[0-9]+\.[0-9]+\.[0-9]+"$')
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def reported_versions() -> set:
    """Every version the package reports, read from this checkout's src/."""
    sys.path.insert(0, str(Path("src").resolve()))
    import subtitle_skill
    from subtitle_skill.contract import build_contract
    from subtitle_skill.doctor import build_doctor_report

    return {subtitle_skill.__version__, build_contract()["version"], build_doctor_report()["version"]}


def main() -> int:
    new_version = os.environ.get("NEW_VERSION", "")
    if not SEMVER_RE.match(new_version):
        print(f"::error::NEW_VERSION {new_version!r} is not a plain X.Y.Z semver", file=sys.stderr)
        return 1
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = VERSION_LINE_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        print('::error::expected a `version = "X.Y.Z"` line in pyproject.toml', file=sys.stderr)
        return 1
    PYPROJECT.write_text(new_text, encoding="utf-8")
    print(f"pyproject.toml version -> {new_version}")
    reported = reported_versions()
    if reported != {new_version}:
        print(f"::error::after the bump the package still reports {sorted(reported)}, not {new_version}", file=sys.stderr)
        return 1
    print(f"__version__, contract --json and doctor --json report {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
