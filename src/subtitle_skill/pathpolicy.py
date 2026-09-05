"""Workspace-confined path resolution.

subtitle-skill enforces its own path security independent of any caller
(video-production-agent is assumed to have a PathPolicy too, but this is
defense in depth, not a substitute).

Rules:
- every input/output path is relative to a single `workspace_root`.
- absolute paths in a request are rejected.
- ".." components are rejected before any filesystem call.
- the resolved, canonical (symlink-following) path must still be inside
  the canonical workspace root -- this catches a symlink planted inside
  the workspace that escapes it.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath, PurePosixPath

from .errors import SubtitleSkillError

_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def _reject_traversal(raw: str) -> None:
    if not raw or not isinstance(raw, str):
        raise SubtitleSkillError("PATH_NOT_ALLOWED", "path must be a non-empty string")
    if "\x00" in raw:
        raise SubtitleSkillError("PATH_NOT_ALLOWED", "path contains a NUL byte")
    if PureWindowsPath(raw).is_absolute() or PurePosixPath(raw).is_absolute():
        raise SubtitleSkillError("PATH_NOT_ALLOWED", f"absolute paths are not allowed: {raw!r}")
    if PureWindowsPath(raw).drive:
        raise SubtitleSkillError("PATH_NOT_ALLOWED", f"drive-qualified paths are not allowed: {raw!r}")
    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SubtitleSkillError("PATH_NOT_ALLOWED", f"path traversal ('..') is not allowed: {raw!r}")
    for p in parts:
        stem = p.split(".")[0].upper()
        if stem in _RESERVED_WINDOWS_NAMES:
            raise SubtitleSkillError("PATH_NOT_ALLOWED", f"reserved device name in path: {p!r}")


class PathPolicy:
    """Confines every resolved path to a single canonical root directory."""

    def __init__(self, workspace_root: str | os.PathLike):
        root = Path(workspace_root)
        if not root.is_absolute():
            raise SubtitleSkillError("PATH_NOT_ALLOWED", "workspace_root must be an absolute path")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)

    def resolve_input(self, relative_path: str) -> Path:
        _reject_traversal(relative_path)
        candidate = (self.root / relative_path)
        if not candidate.exists():
            raise SubtitleSkillError("MISSING_INPUT", f"input not found: {relative_path!r}")
        resolved = candidate.resolve(strict=True)
        self._require_within_root(resolved, relative_path)
        return resolved

    def resolve_output(self, relative_path: str) -> Path:
        _reject_traversal(relative_path)
        candidate = self.root / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = candidate.parent.resolve(strict=True)
        self._require_within_root(resolved_parent, relative_path)
        return resolved_parent / candidate.name

    def _require_within_root(self, resolved: Path, original: str) -> None:
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise SubtitleSkillError(
                "PATH_NOT_ALLOWED", f"path escapes workspace root (possible symlink escape): {original!r}"
            ) from None
