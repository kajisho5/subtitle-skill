"""subtitle-skill: deterministic subtitle validation, generation and rendering.

This package intentionally contains no AI/LLM logic, no subtitle "editing
judgement", and no arbitrary FFmpeg execution. See README.md for the full
responsibility boundary.
"""

def _version() -> str:
    """The package version, from one place: this checkout's pyproject.toml when running from source,
    otherwise the installed distribution's metadata. Releases bump only pyproject.toml."""
    import re as _re
    from pathlib import Path as _Path
    pyproject = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if _re.search(r'^name\s*=\s*"subtitle-skill"', text, _re.M):
            found = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.M)
            if found:
                return found.group(1)
    import sys as _sys
    for base in _sys.path:
        for meta in sorted(_Path(base or ".").glob("subtitle_skill-*.dist-info/METADATA")):
            found = _re.search(r"^Version: *(\S+)", meta.read_text(encoding="utf-8", errors="replace"), _re.M)
            if found:
                return found.group(1)
    return "0.0.0+unknown"


__version__ = _version()

SKILL_ID = "subtitle-skill"
SKILL_VERSION = __version__
CONTRACT_VERSION = "1.0.0"
