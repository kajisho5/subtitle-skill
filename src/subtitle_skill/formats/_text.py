"""Shared cue text helpers for the generators."""
from __future__ import annotations

from ..models import SubtitleCue

_CJK = ("ja", "zh", "ko")


def display_segments(cue: SubtitleCue) -> list:
    """The cue's drawn text as [(segment, ruby reading or None)], with the
    `Speaker: ` prefix (when the cue has a speaker) in front of the first one."""
    segments = cue.ruby_segments()
    if cue.speaker:
        segments = [(f"{cue.speaker}: ", None)] + segments
    return segments


def ruby_as_text(base: str, reading: str, language: str) -> str:
    """Ruby written inline, for a format that has none (SRT, EBU-TT-D): the reading
    in brackets after its base, full-width brackets for ja/zh/ko."""
    if language.split("-", 1)[0].lower() in _CJK:
        return f"{base}（{reading}）"
    return f"{base} ({reading})"


def plain_text(cue: SubtitleCue, language: str) -> str:
    return "".join(seg if reading is None else ruby_as_text(seg, reading, language)
                   for seg, reading in display_segments(cue))
