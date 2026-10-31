"""subtitle-skill: deterministic subtitle validation, generation and rendering.

This package intentionally contains no AI/LLM logic, no subtitle "editing
judgement", and no arbitrary FFmpeg execution. See README.md for the full
responsibility boundary.
"""

PACKAGE_NAME = "subtitle-skill"


def _version() -> str:
    """The package version, from one place: pyproject.toml (the only file the release workflow bumps --
    docs/adr/ADR-003-single-source-version.md).

    From a source checkout (including an editable install, whose recorded metadata goes stale on the next
    release bump) it is read straight from this checkout's pyproject.toml; from an installed wheel, from the
    distribution metadata (importlib.metadata) that was built from that same pyproject.toml. If neither is
    found it reports 0.0.0+unknown rather than a plausible but wrong number.
    """
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if re.search(r'^name\s*=\s*"subtitle-skill"\s*$', text, re.M):
            found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.M)
            if found:
                return found.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8 is not supported
        return "0.0.0+unknown"
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _version()

SKILL_ID = "subtitle-skill"
SKILL_VERSION = __version__
CONTRACT_VERSION = "1.0.0"
