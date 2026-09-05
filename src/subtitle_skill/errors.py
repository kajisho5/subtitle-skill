"""Typed error model for subtitle-skill.

Error codes follow the shared Skill-ecosystem convention (typed, stable
identifiers rather than free-form strings) so callers such as
video-production-agent can branch on `code` instead of parsing messages.
"""
from __future__ import annotations

from typing import Any


#: Error codes that must never be produced by normal operation without a
#: caller-actionable reason. Every raised error picks exactly one of these.
ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "INVALID_INPUT",
        "UNSUPPORTED_OPERATION",
        "UNSUPPORTED_FORMAT",
        "INVALID_TIME_RANGE",
        "DEPENDENCY_ERROR",
        "PATH_NOT_ALLOWED",
        "MISSING_INPUT",
        "OUTPUT_ERROR",
        "VALIDATION_ERROR",
        "TOOL_ERROR",
        "CANCELLED",
        "INTERNAL_ERROR",
    }
)

#: Codes that represent a caller mistake or unfixable-by-retry state.
#: Everything else is considered retryable (e.g. a transient DEPENDENCY_ERROR
#: or TOOL_ERROR talking to an execution skill).
NON_RETRYABLE_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "INVALID_INPUT",
        "UNSUPPORTED_OPERATION",
        "UNSUPPORTED_FORMAT",
        "INVALID_TIME_RANGE",
        "PATH_NOT_ALLOWED",
        "MISSING_INPUT",
        "VALIDATION_ERROR",
        "CANCELLED",
    }
)


class SubtitleSkillError(Exception):
    """Base exception for all subtitle-skill failures.

    Carries a typed `code`, a human message, and optional structured
    `details` (e.g. a list of validation issues) so the CLI layer can
    serialize a consistent error envelope.
    """

    def __init__(self, code: str, message: str, *, details: Any = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def retryable(self) -> bool:
        return self.code not in NON_RETRYABLE_CODES

    def to_dict(self) -> dict:
        d = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            d["details"] = self.details
        return d
