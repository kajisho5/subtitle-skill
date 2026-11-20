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


def _style_font(font: Optional[str], language: str) -> str:
    """The Fontname of the one style: `font` (the family `render` read from the
    caller's `fonts_dir`) or the family for the language. A style line is comma
    separated, so a family with a comma or a line break cannot be named."""
    if font is None:
        return font_for(language)
    if not isinstance(font, str) or not font.strip() or any(ch in font for ch in ",\r\n"):
        raise SubtitleSkillError("INVALID_INPUT", f"font family cannot be named in an ASS style: {font!r}")
    return font.strip()


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


def _cue_body(cue: SubtitleCue, marks=None, look=None) -> str:
    text = "".join(seg for seg, _reading in display_segments(cue))
    body = _marked_text(cue, text, marks, look) if marks else escape_text(text)
    tags = _overrides(cue) + (look.event_tags(cue.id) if look is not None else "")
    return ("{" + tags + "}" if tags else "") + body


_SPACES = re.compile(r"([ \t\n]+)")


def _marked_text(cue: SubtitleCue, text: str, marks, look) -> str:
    """`text` with the tokens at `marks` (indexes of its space-separated tokens; a no-break
    space does not separate) drawn in the highlight colour and bold, then set back to the
    cue's own colour and weight (docs/adr/ADR-013-caption-quality.md, emphasis)."""
    from ..emphasis import split_edges

    restore_color = ass_color(cue.style.color if cue.style is not None and cue.style.color else look.base_color)
    restore_bold = 1 if (cue.style is not None and cue.style.bold) or look.base_bold else 0
    highlight = ass_color(look.highlight_color)
    out = ""
    k = 0
    for piece in _SPACES.split(text):
        if not piece:
            continue
        if _SPACES.fullmatch(piece):
            out += escape_text(piece)
            continue
        if k in marks:
            lead, core, trail = split_edges(piece)
            out += (escape_text(lead) + "{\\c" + highlight[:2] + highlight[4:] + "&\\b1}" + escape_text(core)
                    + "{\\c" + restore_color[:2] + restore_color[4:] + f"&\\b{restore_bold}}}" + escape_text(trail))
        else:
            out += escape_text(piece)
        k += 1
    return out


class Look:
    """Per-cue emphasis marks and legibility treatments for an ASS burn
    (docs/adr/ADR-013-caption-quality.md). Empty, it changes nothing."""

    def __init__(self, *, marks=None, highlight_color: str = "FFD200", treatments=None, outline: int = 1,
                 base_color: Optional[str] = None, base_bold: bool = False, boxes=None):
        self.marks = marks or {}
        self.highlight_color = highlight_color
        self.treatments = treatments or {}
        self.outline = outline
        self.base_color = base_color or DEFAULT_COLOR
        self.base_bold = base_bold
        self.boxes = boxes or {}

    def event_tags(self, cue_id: str) -> str:
        if self.treatments.get(cue_id) == "halo":
            from ..legibility import HALO_BLUR_SCALE, HALO_OUTLINE_SCALE

            return f"\\bord{max(2, round(self.outline * HALO_OUTLINE_SCALE))}\\blur{max(1, round(self.outline * HALO_BLUR_SCALE))}"
        return ""

    def scrim_event(self, cue_id: str, start: str, end: str) -> Optional[str]:
        """A scrim: one semi-opaque dark rectangle on layer 0 over the cue's measured text box
        (a BorderStyle 3 box is drawn per glyph run, so a highlighted word left darker seams)."""
        box = self.boxes.get(cue_id) if self.treatments.get(cue_id) == "scrim" else None
        if box is None:
            return None
        from ..legibility import SCRIM_ALPHA

        x, y, w, h = box
        return (f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\an7\\pos({x},{y})\\p1\\bord0\\shad0"
                f"\\1c&H000000&\\1a&H{SCRIM_ALPHA:02X}&}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

    def layer(self, cue_id: str) -> int:
        return 1 if self.treatments.get(cue_id) == "scrim" else 0


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
    font: Optional[str] = None,
    look: Optional[Look] = None,
    wrap: bool = False,
) -> str:
    """The document as one ASS script for a `width` x `height` video.

    `positions` maps a cue id to "bottom" (default) or "top". `style` is the
    document-wide color/bold/size `render` already reduced (size in percent of
    the 288-line script, as for the SRT path). `look` carries emphasis marks and
    legibility treatments, and `wrap` lets libass wrap long lines (WrapStyle 0; it
    never breaks at a no-break space) instead of only at the text's own breaks;
    both are for docs/adr/ADR-013-caption-quality.md and change nothing when unset."""
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
    if look is not None:
        look.outline = outline
        look.base_color = color or DEFAULT_COLOR
        look.base_bold = bold
    header = [
        "[Script Info]",
        "; written by subtitle-skill",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        f"WrapStyle: {0 if wrap else 2}",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{_style_font(font, document.language)},{font_size},{ass_color(color)},{ass_color(color)},"
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
        layer = 0
        if look is not None:
            scrim = look.scrim_event(cue.id, start, end)
            if scrim is not None:
                events.append(scrim)
            layer = look.layer(cue.id)
        if cue.ruby:
            ruby = _ruby_events(cue, start=start, end=end, position=position, width=width,
                                height=height, font_size=font_size, margin_v=margin_v)
            if look is not None:
                # the line events are layer 0 and the readings layer 1; over a scrim, one higher
                tags = look.event_tags(cue.id)
                ruby = [f"Dialogue: {int(e[10]) + layer}," + e[12:].replace("{\\an2", "{\\an2" + tags, 1)
                        for e in ruby]
            events.extend(ruby)
            continue
        prefix = "{\\an8}" if position == "top" else ""
        marks = look.marks.get(cue.id) if look is not None else None
        events.append(f"Dialogue: {layer},{start},{end},Default,,0,0,0,,{prefix}{_cue_body(cue, marks, look)}")
    return "\n".join(header + events) + "\n"


def generate_shorts_ass(document: SubtitleDocument, *, width: int, height: int,
                        positions: Optional[Mapping[str, str]] = None, font: Optional[str] = None,
                        look: Optional[Look] = None) -> str:
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
    if look is not None:
        look.outline = outline
        look.base_color = DEFAULT_COLOR
        look.base_bold = True
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
        f"Style: Default,{_style_font(font, document.language)},{layout.font_size},{ass_color(None)},{ass_color(None)},"
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
        marks = look.marks.get(cue.id) if look is not None else None
        tags = _overrides(cue) + (look.event_tags(cue.id) if look is not None else "")
        body = _marked_text(cue, text, marks, look) if marks else escape_text(text)
        layer = 0
        if look is not None:
            scrim = look.scrim_event(cue.id, _timestamp(cue.start), _timestamp(cue.end))
            if scrim is not None:
                events.append(scrim)
            layer = look.layer(cue.id)
        events.append(
            f"Dialogue: {layer},{_timestamp(cue.start)},{_timestamp(cue.end)},Default,,0,0,0,,"
            f"{{\\an2\\pos({cx:.0f},{y:.0f})\\fs{size}{tags}}}{body}"
        )
    return "\n".join(header + events) + "\n"
