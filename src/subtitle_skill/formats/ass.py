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
import unicodedata
from typing import Mapping, Optional

from ..errors import SubtitleSkillError
from ..models import SubtitleCue, SubtitleDocument, SubtitleStyle
from ._text import display_segments

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


def _overrides(cue: SubtitleCue) -> str:
    """Per-event style: bold/italic and a cue's own colour (a regional profile's
    speaker colour arrives as one)."""
    tags = ""
    if cue.style is not None:
        if cue.style.bold:
            tags += "\\b1"
        if cue.style.italic:
            tags += "\\i1"
        if cue.style.color is not None:
            tags += f"\\c{ass_color(cue.style.color)[:2]}{ass_color(cue.style.color)[4:]}&"
    return tags


def _cue_body(cue: SubtitleCue) -> str:
    body = escape_text("".join(seg for seg, _reading in display_segments(cue)))
    tags = _overrides(cue)
    return ("{" + tags + "}" if tags else "") + body


#: libass sizes a font by its ascent + descent; Noto Sans CJK's is 1.448 em, so one
#: full-width character advances about 0.69 x Fontsize (measured on a libass burn:
#: ten 国 at Fontsize 60 draw 407 px wide). Half-width characters are taken as half that.
CJK_EM_PER_FONTSIZE = 1 / 1.448
RUBY_SCALE = 0.5
LINE_ADVANCE = 1.25


def _advance(text: str, font_size: float) -> float:
    em = font_size * CJK_EM_PER_FONTSIZE
    return sum(em if unicodedata.east_asian_width(ch) in ("W", "F") else em * 0.5 for ch in text)


def _ruby_events(cue: SubtitleCue, *, start: str, end: str, position: str, width: int, height: int,
                 font_size: int, margin_v: int) -> list:
    """A cue with ruby, laid out by subtitle-skill: one event per line at a fixed
    `\\pos` (bottom centre of the line), and one smaller event per reading centred
    over its base. libass has no ruby; the base's x is estimated from the
    characters' East Asian Width (CJK_EM_PER_FONTSIZE), so a reading can sit a few
    pixels off over proportional Latin text."""
    lines: list = [[]]
    for segment, reading in display_segments(cue):
        parts = segment.split("\n") if reading is None else [segment]
        for i, part in enumerate(parts):
            if i:
                lines.append([])
            if part:
                lines[-1].append((part, reading))
    ruby_size = max(1, round(font_size * RUBY_SCALE))
    pitch = font_size * LINE_ADVANCE + ruby_size
    tags = _overrides(cue)
    cx = width / 2.0
    events = []
    n = len(lines)
    for k, line in enumerate(lines):
        if position == "top":
            y = margin_v + ruby_size + font_size * LINE_ADVANCE + k * pitch
        else:
            y = height - margin_v - (n - 1 - k) * pitch
        text = "".join(seg for seg, _r in line)
        events.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an2\\pos({cx:.0f},{y:.0f}){tags}}}{escape_text(text)}"
        )
        x = cx - _advance(text, font_size) / 2.0
        for seg, reading in line:
            seg_w = _advance(seg, font_size)
            if reading is not None:
                rx, ry = x + seg_w / 2.0, y - font_size * 0.95
                events.append(
                    f"Dialogue: 1,{start},{end},Default,,0,0,0,,"
                    f"{{\\an2\\pos({rx:.0f},{ry:.0f})\\fs{ruby_size}{tags}}}{escape_text(reading)}"
                )
            x += seg_w
    return events


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
        if cue.style is not None and cue.style.vertical:
            raise SubtitleSkillError(
                "UNSUPPORTED_FORMAT", f"cue {cue.id}: vertical text is written to TTML/WebVTT, not burned in"
            )
        start, end = _timestamp(cue.start), _timestamp(cue.end)
        if cue.ruby:
            events.extend(_ruby_events(cue, start=start, end=end, position=position, width=width,
                                       height=height, font_size=font_size, margin_v=margin_v))
            continue
        prefix = "{\\an8}" if position == "top" else ""
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{prefix}{_cue_body(cue)}")
    return "\n".join(header + events) + "\n"


def generate_shorts_ass(document: SubtitleDocument, *, width: int, height: int,
                        positions: Optional[Mapping[str, str]] = None) -> str:
    """The `shorts` caption profile (roadmap step 11, ADR-009) as ASS: one event per
    one-or-two-word cue, bold, large, at a fixed `\\\\pos` inside the 9:16 safe zone
    (`shorts.ShortsLayout`), drawn smaller when it would not fit the safe width."""
    from ..shorts import OUTLINE, ShortsLayout

    if width <= 0 or height <= 0:
        raise SubtitleSkillError("INVALID_INPUT", "ASS burn needs the video's width and height")
    layout = ShortsLayout(width, height)
    boxes = layout.caption_boxes()
    positions = positions or {}
    outline = max(1, round(OUTLINE * height))
    header = [
        "[Script Info]",
        "; written by subtitle-skill (caption_profile shorts)",
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
        f"Style: Default,{font_for(document.language)},{layout.font_size},{ass_color(None)},{ass_color(None)},"
        f"{ass_color(DEFAULT_OUTLINE_COLOR)},{ass_color(DEFAULT_OUTLINE_COLOR, alpha=0x80)},"
        f"-1,0,0,0,100,100,0,0,1,{outline},0,2,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    cx = layout.centre_x()
    for cue in sorted(document.cues, key=lambda c: (c.start, c.end, c.id)):
        if cue.style is not None and cue.style.vertical:
            raise SubtitleSkillError("UNSUPPORTED_FORMAT", f"cue {cue.id}: vertical text is not burned in")
        box = boxes[positions.get(cue.id, "bottom")]
        y = (box.y + box.h) * height
        text = "".join(seg for seg, _r in display_segments(cue))
        size = layout.fit_font_size(text)
        events.append(
            f"Dialogue: 0,{_timestamp(cue.start)},{_timestamp(cue.end)},Default,,0,0,0,,"
            f"{{\\an2\\pos({cx:.0f},{y:.0f})\\fs{size}{_overrides(cue)}}}{escape_text(text)}"
        )
    return "\n".join(header + events) + "\n"
