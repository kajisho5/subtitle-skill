"""Agent Skill installer: places `SKILL.md` where Claude Code / Cursor /
Codex discover skills, mirroring ffmpeg-skill's `bin/install.js` convention
(same flags: `--claude`, `--cursor`, `--codex`, `--project`, `--dir`,
`--all`, `--uninstall`; same default install targets).

Unlike ffmpeg-skill -- whose tools are standalone stdlib scripts, copied
whole into the skill directory and run directly with no install step --
subtitle-skill is a regular installed Python package with relative
imports. The `subtitle-skill` command must already be on PATH
(`pip install subtitle-skill`, or `pip install -e .` from a checkout)
for an agent to actually invoke it; this installer places only the
discovery document, `SKILL.md`, and says so rather than pretending it
copies a self-contained runtime.

`SKILL.md` is packaged inside `subtitle_skill/SKILL.md` (not read from a
repo-root path, which would not exist after a real `pip install`) --
`tests/test_installer.py` asserts it stays byte-identical to the
repo-root `SKILL.md` that GitHub and humans read, so the two can never
silently drift apart.
"""
from __future__ import annotations

import importlib.resources
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SKILL_NAME = "subtitle-skill"

def _agent_targets() -> dict:
    """Directories the skill can be installed into, keyed by CLI flag name.

    Computed lazily (not at import time) so `Path.home()` is read fresh on
    every call -- both because a long-lived process could see `$HOME`
    change, and so tests can monkeypatch it. Matches ffmpeg-skill's own
    installer targets exactly (same ecosystem convention -- see
    engine.py's ffmpeg-skill discovery for the same list used in reverse,
    to *find* an ffmpeg-skill install).
    """
    home = Path.home()
    return {
        "claude": ("Claude Code", home / ".claude" / "skills" / SKILL_NAME),
        "cursor": ("Cursor", home / ".cursor" / "skills" / SKILL_NAME),
        "codex": ("Codex", home / ".codex" / "skills" / SKILL_NAME),
    }


@dataclass(frozen=True)
class InstallResult:
    label: str
    directory: str
    action: str  # "installed" | "removed" | "failed"
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"label": self.label, "directory": self.directory, "action": self.action}
        if self.error is not None:
            d["error"] = self.error
        return d


def _skill_md_bytes() -> bytes:
    return importlib.resources.files("subtitle_skill").joinpath("SKILL.md").read_bytes()


def _targets(*, claude: bool, cursor: bool, codex: bool, project: bool, custom_dir: Optional[str], all_: bool) -> List[tuple]:
    if all_:
        claude = cursor = codex = True
    targets = _agent_targets()
    selected = []
    for flag, want in (("claude", claude), ("cursor", cursor), ("codex", codex)):
        if want:
            selected.append(targets[flag])
    if project:
        selected.append(("project (.claude/skills)", Path.cwd() / ".claude" / "skills" / SKILL_NAME))
    if custom_dir:
        selected.append(("custom", Path(custom_dir).resolve() / SKILL_NAME))
    if not selected:
        # No target requested: default to Claude Code, matching ffmpeg-skill's installer default.
        selected.append(targets["claude"])
    return selected


def install(
    *,
    claude: bool = False,
    cursor: bool = False,
    codex: bool = False,
    project: bool = False,
    custom_dir: Optional[str] = None,
    all_: bool = False,
    uninstall: bool = False,
) -> List[InstallResult]:
    """Copy (or remove) `SKILL.md` into the requested agent skill
    directories. Returns one `InstallResult` per target; never raises for
    a single target's failure (mirrors ffmpeg-skill's installer, which
    reports per-target success/failure rather than aborting the batch).
    """
    results: List[InstallResult] = []
    payload = None if uninstall else _skill_md_bytes()

    for label, directory in _targets(
        claude=claude, cursor=cursor, codex=codex, project=project, custom_dir=custom_dir, all_=all_
    ):
        try:
            if uninstall:
                shutil.rmtree(directory, ignore_errors=True)
                results.append(InstallResult(label=label, directory=str(directory), action="removed"))
                continue
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "SKILL.md").write_bytes(payload)
            results.append(InstallResult(label=label, directory=str(directory), action="installed"))
        except OSError as exc:
            results.append(InstallResult(label=label, directory=str(directory), action="failed", error=str(exc)))

    return results
