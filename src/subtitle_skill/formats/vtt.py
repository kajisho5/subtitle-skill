"""WebVTT (.vtt) generation.

WebVTT supports cue settings (align/position/line/size) natively; `color`
has no simple, injection-safe representation here (it would require
inline `<c.class>` markup plus a companion STYLE block) so it is rejected
rather than silently dropped or turned into free-form CSS.
"""
from __future__ import annotations

from ..errors import SubtitleSkillError
from ..models import SubtitleDocument, SubtitleCue


def _format_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _cue_settings(cue: SubtitleCue) -> str:
    if cue.style is None:
        return ""
    if cue.style.color is not None:
        raise SubtitleSkillError(
            "UNSUPPORTED_FORMAT", f"cue {cue.id}: WebVTT generation does not support style.color"
        )
    parts = []
    if cue.style.align is not None:
        parts.append(f"align:{cue.style.align}")
    if cue.style.position is not None:
        parts.append(f"position:{cue.style.position:g}%")
    if cue.style.line is not None:
        parts.append(f"line:{cue.style.line:g}%")
    if cue.style.size is not None:
        parts.append(f"size:{cue.style.size:g}%")
    return (" " + " ".join(parts)) if parts else ""


def _escape_text(cue: SubtitleCue) -> str:
    lines = cue.text.split("\n")
    if cue.speaker:
        lines[0] = f"{cue.speaker}: {lines[0]}"
    text = "\n".join(lines)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if cue.style is not None:
        if cue.style.bold:
            text = f"<b>{text}</b>"
        if cue.style.italic:
            text = f"<i>{text}</i>"
    return text


def generate_vtt(document: SubtitleDocument) -> str:
    ordered = sorted(document.cues, key=lambda c: (c.start, c.end, c.id))
    lines = ["WEBVTT", ""]
    for cue in ordered:
        lines.append(cue.id)
        lines.append(f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}{_cue_settings(cue)}")
        lines.append(_escape_text(cue))
        lines.append("")
    return "\n".join(lines) + ("\n" if len(lines) > 2 else "")
