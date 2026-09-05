"""Timeline and text validation.

Design rule: fatal problems (would produce a nonsensical or unrenderable
subtitle) raise VALIDATION_ERROR / INVALID_TIME_RANGE immediately. Soft
problems (e.g. overlapping cues) are never auto-fixed -- they are reported
as `ValidationIssue`s in the response so the caller (video-production-agent
or a human) decides what to do.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .errors import SubtitleSkillError
from .models import SubtitleDocument, SubtitleCue

#: Fields a caller may set. Nothing here is a "domain truth" -- these are
#: just defaults used when the caller supplies no constraints at all.
DEFAULT_MAX_CHARS_PER_LINE = 42
DEFAULT_MAX_LINES = 2
DEFAULT_MIN_DURATION = 0.5
DEFAULT_MAX_DURATION = 10.0
DEFAULT_READING_SPEED_CPS = 20.0  # characters per second

_ALLOWED_CONSTRAINT_KEYS = frozenset(
    {"max_chars_per_line", "max_lines", "min_duration", "max_duration", "reading_speed_cps"}
)

_CONTROL_CHAR_ALLOW = {"\n"}


@dataclass(frozen=True)
class SubtitleConstraints:
    max_chars_per_line: Optional[int] = None
    max_lines: Optional[int] = None
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    reading_speed_cps: Optional[float] = None

    @staticmethod
    def from_dict(data: Optional[Mapping[str, Any]]) -> "SubtitleConstraints":
        if data is None:
            return SubtitleConstraints()
        if not isinstance(data, Mapping):
            raise SubtitleSkillError("INVALID_INPUT", "constraints must be an object")
        unknown = set(data) - _ALLOWED_CONSTRAINT_KEYS
        if unknown:
            raise SubtitleSkillError("INVALID_INPUT", f"unknown constraint field(s): {sorted(unknown)}")
        for key in ("max_chars_per_line", "max_lines"):
            if key in data and data[key] is not None:
                if not isinstance(data[key], int) or isinstance(data[key], bool) or data[key] <= 0:
                    raise SubtitleSkillError("INVALID_INPUT", f"constraints.{key} must be a positive integer")
        for key in ("min_duration", "max_duration", "reading_speed_cps"):
            if key in data and data[key] is not None:
                if not isinstance(data[key], (int, float)) or isinstance(data[key], bool) or data[key] <= 0:
                    raise SubtitleSkillError("INVALID_INPUT", f"constraints.{key} must be a positive number")
        return SubtitleConstraints(**{k: data.get(k) for k in _ALLOWED_CONSTRAINT_KEYS})


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    cue_id: Optional[str]
    message: str

    def to_dict(self) -> dict:
        d = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.cue_id is not None:
            d["cue_id"] = self.cue_id
        return d


def _validate_text(cue: SubtitleCue, constraints: SubtitleConstraints) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    text = cue.text

    if text.strip() == "":
        issues.append(ValidationIssue("error", "EMPTY_TEXT", cue.id, "cue text is empty or whitespace-only"))
        return issues

    for ch in text:
        if ch in _CONTROL_CHAR_ALLOW:
            continue
        if unicodedata.category(ch) == "Cc":
            issues.append(
                ValidationIssue("error", "INVALID_CONTROL_CHARACTER", cue.id, f"disallowed control character: {ch!r}")
            )
            break

    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        issues.append(ValidationIssue("error", "INVALID_UNICODE", cue.id, "text is not valid Unicode"))

    lines = text.split("\n")
    max_lines = constraints.max_lines or DEFAULT_MAX_LINES
    if len(lines) > max_lines:
        issues.append(
            ValidationIssue(
                "warning", "TOO_MANY_LINES", cue.id, f"{len(lines)} lines exceeds max_lines={max_lines}"
            )
        )

    max_chars = constraints.max_chars_per_line or DEFAULT_MAX_CHARS_PER_LINE
    for i, line in enumerate(lines):
        if len(line) > max_chars:
            issues.append(
                ValidationIssue(
                    "warning",
                    "LINE_TOO_LONG",
                    cue.id,
                    f"line {i} has {len(line)} chars, exceeds max_chars_per_line={max_chars}",
                )
            )

    return issues


def _validate_duration(cue: SubtitleCue, constraints: SubtitleConstraints) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    min_d = constraints.min_duration or DEFAULT_MIN_DURATION
    max_d = constraints.max_duration or DEFAULT_MAX_DURATION
    if cue.duration < min_d:
        issues.append(
            ValidationIssue("warning", "CUE_TOO_SHORT", cue.id, f"duration {cue.duration:.3f}s < min_duration={min_d}")
        )
    if cue.duration > max_d:
        issues.append(
            ValidationIssue("warning", "CUE_TOO_LONG", cue.id, f"duration {cue.duration:.3f}s > max_duration={max_d}")
        )

    cps = constraints.reading_speed_cps or DEFAULT_READING_SPEED_CPS
    text_len = len(cue.text.replace("\n", ""))
    if cue.duration > 0 and text_len / cue.duration > cps:
        issues.append(
            ValidationIssue(
                "warning",
                "READING_SPEED_TOO_HIGH",
                cue.id,
                f"{text_len} chars in {cue.duration:.3f}s exceeds reading_speed_cps={cps}",
            )
        )
    return issues


def validate_document(
    document: SubtitleDocument,
    *,
    constraints: Optional[SubtitleConstraints] = None,
    video_duration: Optional[float] = None,
) -> list[ValidationIssue]:
    """Validate a document's timeline and text. Raises on fatal problems,
    returns the full issue list (errors + warnings) otherwise so the caller
    can inspect warnings even on success.
    """
    constraints = constraints or SubtitleConstraints()
    issues: list[ValidationIssue] = []

    ordered = sorted(document.cues, key=lambda c: (c.start, c.end))
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start < prev.end:
            issues.append(
                ValidationIssue(
                    "warning",
                    "CUE_OVERLAP",
                    cur.id,
                    f"cue {cur.id} [{cur.start},{cur.end}) overlaps cue {prev.id} [{prev.start},{prev.end})",
                )
            )

    if video_duration is not None:
        for cue in document.cues:
            if cue.end > video_duration + 1e-6:
                issues.append(
                    ValidationIssue(
                        "error",
                        "CUE_EXCEEDS_VIDEO_DURATION",
                        cue.id,
                        f"cue end {cue.end} exceeds video_duration {video_duration}",
                    )
                )

    for cue in document.cues:
        issues.extend(_validate_text(cue, constraints))
        issues.extend(_validate_duration(cue, constraints))

    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SubtitleSkillError(
            "VALIDATION_ERROR",
            f"{len(errors)} validation error(s) in subtitle document",
            details=[i.to_dict() for i in issues],
        )

    return issues
