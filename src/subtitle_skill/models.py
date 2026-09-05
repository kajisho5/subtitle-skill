"""Typed subtitle data model.

subtitle-skill never processes subtitles as bare dicts. Every cue and
document is a typed, immutable value object built by `from_dict`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

from .errors import SubtitleSkillError

#: style is an allowlisted, typed set of fields -- never free-form.
ALLOWED_STYLE_KEYS = frozenset(
    {"align", "position", "line", "size", "bold", "italic", "color"}
)
ALLOWED_ALIGN = frozenset({"left", "center", "right"})


def _require(cond: bool, code: str, message: str) -> None:
    if not cond:
        raise SubtitleSkillError(code, message)


@dataclass(frozen=True)
class SubtitleStyle:
    align: Optional[str] = None
    position: Optional[float] = None  # 0..100, percent from left/top
    line: Optional[float] = None  # 0..100, percent from top
    size: Optional[float] = None  # 0..100, percent
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color: Optional[str] = None

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SubtitleStyle":
        unknown = set(data) - ALLOWED_STYLE_KEYS
        _require(
            not unknown,
            "INVALID_INPUT",
            f"unknown style field(s): {sorted(unknown)}",
        )
        if "align" in data and data["align"] is not None:
            _require(
                data["align"] in ALLOWED_ALIGN,
                "INVALID_INPUT",
                f"style.align must be one of {sorted(ALLOWED_ALIGN)}",
            )
        return SubtitleStyle(**{k: data.get(k) for k in ALLOWED_STYLE_KEYS})

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class SubtitleCue:
    id: str
    start: float  # seconds, inclusive
    end: float  # seconds, exclusive
    text: str
    speaker: Optional[str] = None
    style: Optional[SubtitleStyle] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SubtitleCue":
        _require(isinstance(data, Mapping), "INVALID_INPUT", "cue must be an object")
        for req in ("id", "start", "end", "text"):
            _require(req in data, "MISSING_INPUT", f"cue missing required field: {req}")

        cue_id = data["id"]
        _require(
            isinstance(cue_id, str) and cue_id.strip() != "",
            "INVALID_INPUT",
            "cue.id must be a non-empty string",
        )

        start = data["start"]
        end = data["end"]
        _require(
            isinstance(start, (int, float)) and not isinstance(start, bool),
            "INVALID_TIME_RANGE",
            f"cue {cue_id}: start must be numeric",
        )
        _require(
            isinstance(end, (int, float)) and not isinstance(end, bool),
            "INVALID_TIME_RANGE",
            f"cue {cue_id}: end must be numeric",
        )
        start = float(start)
        end = float(end)
        _require(
            math.isfinite(start) and math.isfinite(end),
            "INVALID_TIME_RANGE",
            f"cue {cue_id}: start/end must be finite (NaN/Infinity not allowed)",
        )
        _require(start >= 0, "INVALID_TIME_RANGE", f"cue {cue_id}: start < 0")
        _require(end > start, "INVALID_TIME_RANGE", f"cue {cue_id}: end <= start")

        text = data["text"]
        _require(isinstance(text, str), "INVALID_INPUT", f"cue {cue_id}: text must be a string")

        speaker = data.get("speaker")
        if speaker is not None:
            _require(
                isinstance(speaker, str) and speaker.strip() != "",
                "INVALID_INPUT",
                f"cue {cue_id}: speaker must be a non-empty string",
            )

        style_data = data.get("style")
        style = SubtitleStyle.from_dict(style_data) if style_data is not None else None

        metadata = data.get("metadata", {})
        _require(
            isinstance(metadata, Mapping), "INVALID_INPUT", f"cue {cue_id}: metadata must be an object"
        )

        unknown = set(data) - {"id", "start", "end", "text", "speaker", "style", "metadata"}
        _require(not unknown, "INVALID_INPUT", f"cue {cue_id}: unknown field(s) {sorted(unknown)}")

        return SubtitleCue(
            id=cue_id,
            start=start,
            end=end,
            text=text,
            speaker=speaker,
            style=style,
            metadata=dict(metadata),
        )

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "start": self.start, "end": self.end, "text": self.text}
        if self.speaker is not None:
            d["speaker"] = self.speaker
        if self.style is not None:
            d["style"] = self.style.to_dict()
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        return d


@dataclass(frozen=True)
class SubtitleDocument:
    id: str
    version: int
    language: str
    cues: tuple  # tuple[SubtitleCue, ...], ordered as provided
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SubtitleDocument":
        _require(isinstance(data, Mapping), "INVALID_INPUT", "subtitle document must be an object")
        for req in ("id", "language", "cues"):
            _require(req in data, "MISSING_INPUT", f"subtitle document missing field: {req}")

        doc_id = data["id"]
        _require(
            isinstance(doc_id, str) and doc_id.strip() != "",
            "INVALID_INPUT",
            "document.id must be a non-empty string",
        )

        version = data.get("version", 1)
        _require(
            isinstance(version, int) and not isinstance(version, bool) and version >= 1,
            "INVALID_INPUT",
            "document.version must be a positive integer",
        )

        language = data["language"]
        _require(
            isinstance(language, str) and _is_bcp47_ish(language),
            "INVALID_INPUT",
            f"document.language is not a valid language tag: {language!r}",
        )

        raw_cues = data["cues"]
        _require(isinstance(raw_cues, list) and len(raw_cues) > 0, "MISSING_INPUT", "document.cues must be a non-empty array")

        cues = tuple(SubtitleCue.from_dict(c) for c in raw_cues)

        seen = set()
        for c in cues:
            _require(c.id not in seen, "VALIDATION_ERROR", f"duplicate cue id: {c.id}")
            seen.add(c.id)

        metadata = data.get("metadata", {})
        _require(isinstance(metadata, Mapping), "INVALID_INPUT", "document.metadata must be an object")

        unknown = set(data) - {"id", "version", "language", "cues", "metadata"}
        _require(not unknown, "INVALID_INPUT", f"unknown document field(s) {sorted(unknown)}")

        return SubtitleDocument(
            id=doc_id, version=version, language=language, cues=cues, metadata=dict(metadata)
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "language": self.language,
            "cues": [c.to_dict() for c in self.cues],
            "metadata": dict(self.metadata),
        }

    def sorted_by_start(self) -> "SubtitleDocument":
        return replace(self, cues=tuple(sorted(self.cues, key=lambda c: (c.start, c.end, c.id))))


def _is_bcp47_ish(tag: str) -> bool:
    if not tag or len(tag) > 35:
        return False
    parts = tag.split("-")
    if not parts[0].isalpha() or not (2 <= len(parts[0]) <= 8):
        return False
    return all(p.isalnum() for p in parts)
