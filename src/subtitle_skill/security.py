"""Request-level security boundary.

Rejects any request that carries a key associated with arbitrary command
execution, regardless of where in the JSON tree it appears or what its
value is. This is checked before any other parsing so a malicious payload
never reaches format/render code.
"""
from __future__ import annotations

from typing import Any

from .errors import SubtitleSkillError

FORBIDDEN_KEYS = frozenset(
    {
        "command",
        "argv",
        "shell",
        "executable",
        "filter",
        "filter_complex",
        "vf",
        "af",
        "env",
        "api_key",
    }
)


def reject_forbidden_keys(data: Any, *, _path: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_KEYS:
                raise SubtitleSkillError(
                    "INVALID_REQUEST",
                    f"forbidden field '{_path + key}' is not allowed in a subtitle-skill request",
                )
            reject_forbidden_keys(value, _path=f"{_path}{key}.")
    elif isinstance(data, list):
        for item in data:
            reject_forbidden_keys(item, _path=_path)
