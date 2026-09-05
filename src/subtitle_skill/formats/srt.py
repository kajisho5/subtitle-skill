"""SubRip (.srt) generation.

SRT has no native representation for cue position/alignment/size. If a cue
carries that style information we refuse to silently drop it -- the caller
must either omit it or choose a format that supports it (WebVTT).

Cue-level `metadata` is provenance/auxiliary data, not renderable text; it
is intentionally never written into the subtitle body of any format.
"""
from __future__ import annotations

from ..errors import SubtitleSkillError
from ..models import SubtitleDocument, SubtitleCue

_UNSUPPORTED_STYLE_FIELDS = ("align", "position", "line", "size", "color")


def _format_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _escape_text(cue: SubtitleCue) -> str:
    lines = cue.text.split("\n")
    if cue.speaker:
        lines[0] = f"{cue.speaker}: {lines[0]}"
    text = "\n".join(lines)

    if cue.style is not None:
        unsupported = [f for f in _UNSUPPORTED_STYLE_FIELDS if getattr(cue.style, f) is not None]
        if unsupported:
            raise SubtitleSkillError(
                "UNSUPPORTED_FORMAT",
                f"cue {cue.id}: SRT cannot represent style field(s) {unsupported}",
            )
        if cue.style.bold:
            text = f"<b>{text}</b>"
        if cue.style.italic:
            text = f"<i>{text}</i>"
    return text


def generate_srt(document: SubtitleDocument) -> str:
    ordered = sorted(document.cues, key=lambda c: (c.start, c.end, c.id))
    blocks = []
    for index, cue in enumerate(ordered, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}",
                    _escape_text(cue),
                    "",
                ]
            )
        )
    return "\n".join(blocks) + ("\n" if blocks else "")
