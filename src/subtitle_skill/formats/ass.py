"""ASS (Advanced SubStation Alpha) for burn-in through ffmpeg-skill `caption --ass`.

Internal: `render` writes this file only when a cue needs per-event layout that
caption.py's whole-burn SRT `force_style` cannot express (roadmap step 8: a cue
moved to the top while a face is low in frame). It is never a `generate` format.

The layout reproduces caption.py's own SRT defaults so a document burns the same
way whichever path it takes: the script is the video's own size (PlayResX/Y),
24 pt type, outline 2 and a 30 unit vertical margin on a 288-line script scaled to
that height, white text on a black outline, bottom centre. A cue moved to the top
gets `{\\an8}` (per-event alignment, top centre).
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

from ..errors import SubtitleSkillError
from ..models import SubtitleCue, SubtitleDocument, SubtitleStyle

SCRIPT_HEIGHT = 288.0
DEFAULT_SIZE_POINTS = 24.0
DEFAULT_OUTLINE_POINTS = 2.0
DEFAULT_MARGIN_POINTS = 30.0
SIDE_MARGIN = 0.05
DEFAULT_COLOR = "FFFFFF"
DEFAULT_OUTLINE_COLOR = "000000"

#: The font family named in the ASS style, by script (libass falls back through
#: fontconfig for any glyph the family lacks; caption.py adds the directory of a
#: font that covers the text as `fontsdir`).
_FONT_BY_LANGUAGE = {"ja": "Noto Sans CJK JP", "zh": "Noto Sans CJK SC", "ko": "Noto Sans CJK KR"}
DEFAULT_FONT = "DejaVu Sans"

_HEX = re.compile(r"^(?:#|0x)?([0-9A-Fa-f]{6})$")


def ass_color(value: Optional[str], *, alpha: int = 0) -> str:
    """'RRGGBB' / '#RRGGBB' / '0xRRGGBB' -> '&HAABBGGRR'."""
    raw = DEFAULT_COLOR if value is None else value
    m = _HEX.match(raw.strip()) if isinstance(raw, str) else None
    if not m:
        raise SubtitleSkillError("INVALID_INPUT", f"colour must be RRGGBB hex, got {value!r}")
    rgb = m.group(1).upper()
    return f"&H{alpha:02X}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def _timestamp(seconds: float) -> str:
    total_cs = round(seconds * 100)
    hours, rem = divmod(total_cs, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def escape_text(text: str) -> str:
    """Plain text as ASS event text: braces would open an override block and a
    backslash an escape, so they are escaped; newlines become \\N."""
    text = text.replace("\\", "\\⁠")  # a backslash that starts no escape
    text = text.replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r\n", "\n").replace("\n", "\\N")


def font_for(language: str) -> str:
    return _FONT_BY_LANGUAGE.get(language.split("-", 1)[0].lower(), DEFAULT_FONT)


def _scaled(points: float, height: int) -> int:
    return max(1, round(points * height / SCRIPT_HEIGHT))


def _cue_body(cue: SubtitleCue) -> str:
    lines = cue.text.split("\n")
    if cue.speaker:
        lines[0] = f"{cue.speaker}: {lines[0]}"
    body = escape_text("\n".join(lines))
    if cue.style is not None:
        if cue.style.bold:
            body = "{\\b1}" + body
        if cue.style.italic:
            body = "{\\i1}" + body
    return body


def generate_ass(
    document: SubtitleDocument,
    *,
    width: int,
    height: int,
    positions: Optional[Mapping[str, str]] = None,
    style: Optional[SubtitleStyle] = None,
) -> str:
    """The document as one ASS script for a `width` x `height` video.

    `positions` maps a cue id to "bottom" (default) or "top". `style` is the
    document-wide color/bold/size `render` already reduced (size in percent of
    the 288-line script, as for the SRT path)."""
    if width <= 0 or height <= 0:
        raise SubtitleSkillError("INVALID_INPUT", "ASS burn needs the video's width and height")
    positions = positions or {}
    size_points = DEFAULT_SIZE_POINTS
    color = None
    bold = False
    if style is not None:
        if style.size is not None:
            if isinstance(style.size, bool) or not isinstance(style.size, (int, float)) or style.size <= 0:
                raise SubtitleSkillError("INVALID_INPUT", f"style.size must be a positive number, got {style.size!r}")
            size_points = style.size / 100.0 * SCRIPT_HEIGHT
        color = style.color
        bold = bool(style.bold)
    font_size = _scaled(size_points, height)
    margin_v = _scaled(DEFAULT_MARGIN_POINTS, height)
    margin_lr = max(1, round(width * SIDE_MARGIN))
    outline = _scaled(DEFAULT_OUTLINE_POINTS, height)
    header = [
        "[Script Info]",
        "; written by subtitle-skill",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_for(document.language)},{font_size},{ass_color(color)},{ass_color(color)},"
        f"{ass_color(DEFAULT_OUTLINE_COLOR)},{ass_color(DEFAULT_OUTLINE_COLOR, alpha=0x80)},"
        f"{-1 if bold else 0},0,0,0,100,100,0,0,1,{outline},0,2,{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    for cue in sorted(document.cues, key=lambda c: (c.start, c.end, c.id)):
        position = positions.get(cue.id, "bottom")
        if position not in ("bottom", "top"):
            raise SubtitleSkillError("INVALID_INPUT", f"cue {cue.id}: unknown position {position!r}")
        prefix = "{\\an8}" if position == "top" else ""
        events.append(
            f"Dialogue: 0,{_timestamp(cue.start)},{_timestamp(cue.end)},Default,,0,0,0,,{prefix}{_cue_body(cue)}"
        )
    return "\n".join(header + events) + "\n"
