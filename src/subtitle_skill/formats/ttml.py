"""TTML output: IMSC 1.1 Text Profile (`ttml`) and EBU-TT-D (`ebu-tt-d`).

Roadmap step 9, docs/adr/ADR-007-ttml-output.md. Both are TTML documents with
`ttp:timeBase="media"`, clock times `hh:mm:ss.fff`, and regions in percent of the
root container (no pixels, so no `tts:extent` on `tt`). The differences:

- IMSC 1.1 Text (W3C Recommendation): `ttp:contentProfiles` names the Text
  profile; text sits in spans inside `p`.
- EBU-TT-D (EBU Tech 3380): `head` must hold `styling` and `layout`, every
  region needs `tts:origin` and `tts:extent`, a `span` holds only text and `br`;
  `ebuttm:documentMetadata` names the standard it conforms to.

Every style is a named `style` referenced by id (EBU-TT-D allows no inline
styling on `p` or `span`). WebVTT cue settings are carried as regions: `line` -> the region's top edge
(`tts:origin` y, `tts:displayAlign="before"`), `position`/`size` -> the region's
horizontal centre and width, `align` -> `tts:textAlign`. `bold`/`italic`/`color`
go on the cue's span (`tts:fontWeight`/`tts:fontStyle`/`tts:color`); unlike SRT and
WebVTT, TTML can carry colour. A cue with none of these uses the bottom region
(the 80 % x 80 % centre of the frame, text at its bottom edge).

`validate_ttml` checks a written document against the structural rules of its
profile with the standard library only (it runs on every output);
`tests/test_ttml.py` additionally validates against the W3C TTML2 XML Schema.
`parse_ttml` reads either profile back into a `SubtitleDocument`.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from ..errors import SubtitleSkillError
from ..models import SubtitleCue, SubtitleDocument, SubtitleStyle

NS_TT = "http://www.w3.org/ns/ttml"
NS_TTP = "http://www.w3.org/ns/ttml#parameter"
NS_TTS = "http://www.w3.org/ns/ttml#styling"
NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_XML = "http://www.w3.org/XML/1998/namespace"
NS_EBUTTM = "urn:ebu:tt:metadata"
NS_EBUTTS = "urn:ebu:tt:style"

IMSC11_TEXT_PROFILE = "http://www.w3.org/ns/ttml/profile/imsc1.1/text"
EBU_TT_D_STANDARD = "urn:ebu:tt:distribution:2018-04"

PROFILES = ("ttml", "ebu-tt-d")

#: the default bottom region: the centre 80 % x 80 % of the frame (EBU-TT-D's
#: own examples use this safe area), text at the bottom edge
_DEFAULT_ORIGIN = (10.0, 10.0)
_DEFAULT_EXTENT = (80.0, 80.0)

_CLOCK = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d|60)(\.\d+)?$")
_PERCENT_PAIR = re.compile(r"^\s*([+]?\d+(?:\.\d+)?)%\s+([+]?\d+(?:\.\d+)?)%\s*$")


def _clock(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _parse_clock(value: str) -> float:
    m = _CLOCK.match(value or "")
    if not m:
        raise SubtitleSkillError("INVALID_INPUT", f"TTML time {value!r} is not hh:mm:ss.fff")
    hours, minutes, secs, frac = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + (float(frac) if frac else 0.0)


def _pct(value: float) -> str:
    return f"{round(value, 3):g}%"


def encode_id(cue_id: str) -> str:
    """A cue id as an XML NCName, reversibly: `cue-` + the id, with every
    character outside [A-Za-z0-9.-] (and `_` itself) written as `_xHHHH_`."""
    out = []
    for ch in cue_id:
        if ch.isascii() and (ch.isalnum() or ch in ".-"):
            out.append(ch)
        else:
            out.append(f"_x{ord(ch):04X}_")
    return "cue-" + "".join(out)


def decode_id(xml_id: str) -> str:
    raw = xml_id[4:] if xml_id.startswith("cue-") else xml_id
    return re.sub(r"_x([0-9A-F]{4,6})_", lambda m: chr(int(m.group(1), 16)), raw)


def _color(value: str) -> str:
    m = re.match(r"^(?:#|0x)?([0-9A-Fa-f]{6})$", value.strip())
    if not m:
        raise SubtitleSkillError("INVALID_INPUT", f"style.color must be RRGGBB hex, got {value!r}")
    return "#" + m.group(1).lower()


def _region_for(style: Optional[SubtitleStyle]) -> tuple:
    """(origin x, origin y, extent w, extent h, displayAlign) in percent."""
    x, y = _DEFAULT_ORIGIN
    w, h = _DEFAULT_EXTENT
    display = "after"
    if style is None:
        return (x, y, w, h, display)
    if style.size is not None:
        w = max(1.0, min(100.0, float(style.size)))
    if style.position is not None:
        centre = max(0.0, min(100.0, float(style.position)))
        x = min(max(0.0, centre - w / 2.0), 100.0 - w)
    elif style.size is not None:
        x = (100.0 - w) / 2.0
    if style.line is not None:
        y = max(0.0, min(99.0, float(style.line)))
        h = 100.0 - y
        display = "before"
    return (x, y, w, h, display)


def _text_align(style: Optional[SubtitleStyle]) -> str:
    return style.align if style is not None and style.align is not None else "center"


def _cue_lines(cue: SubtitleCue) -> list:
    lines = cue.text.split("\n")
    if cue.speaker:
        lines[0] = f"{cue.speaker}: {lines[0]}"
    return lines


def _span_style(cue: SubtitleCue) -> tuple:
    """(bold, italic, colour) of the cue's span: referenced as a named style, since
    EBU-TT-D allows no inline styling on content elements."""
    style = cue.style
    if style is None:
        return (False, False, None)
    return (bool(style.bold), bool(style.italic), _color(style.color) if style.color is not None else None)


def _span(cue: SubtitleCue, style_id: Optional[str]) -> str:
    inner = "<br/>".join(escape(line) for line in _cue_lines(cue))
    open_tag = f'<span style="{style_id}">' if style_id else "<span>"
    return f"{open_tag}{inner}</span>"


def _span_style_attrs(key: tuple) -> str:
    bold, italic, color = key
    attrs = []
    if bold:
        attrs.append('tts:fontWeight="bold"')
    if italic:
        attrs.append('tts:fontStyle="italic"')
    if color is not None:
        attrs.append(f'tts:color="{color}"')
    return " ".join(attrs)


def generate_ttml(document: SubtitleDocument, *, profile: str = "ttml") -> str:
    if profile not in PROFILES:
        raise SubtitleSkillError("UNSUPPORTED_FORMAT", f"unknown TTML profile {profile!r}")
    ordered = sorted(document.cues, key=lambda c: (c.start, c.end, c.id))

    regions: dict = {}  # geometry -> region id
    styles: dict = {}  # textAlign -> style id
    span_styles: dict = {}  # (bold, italic, colour) -> style id
    rows = []
    for cue in ordered:
        geometry = _region_for(cue.style)
        if geometry not in regions:
            positioned = sum(1 for rid in regions.values() if rid != "bottom")
            regions[geometry] = "bottom" if geometry == _region_for(None) else f"r{positioned + 1}"
        region_id = regions[geometry]
        align = _text_align(cue.style)
        style_id = styles.setdefault(align, "s_" + align)
        span_key = _span_style(cue)
        span_id = None
        if span_key != (False, False, None):
            span_id = span_styles.setdefault(span_key, f"s_span{len(span_styles) + 1}")
        rows.append(
            f'      <p xml:id="{encode_id(cue.id)}" begin="{_clock(cue.start)}" end="{_clock(cue.end)}" '
            f'region="{region_id}" style="s_base {style_id}">{_span(cue, span_id)}</p>'
        )
    if not regions:
        regions[_region_for(None)] = "bottom"

    ns = [
        f'xmlns="{NS_TT}"',
        f'xmlns:ttp="{NS_TTP}"',
        f'xmlns:tts="{NS_TTS}"',
        f'xmlns:ttm="{NS_TTM}"',
    ]
    root_attrs = ['ttp:timeBase="media"', f"xml:lang={quoteattr(document.language)}"]
    if profile == "ebu-tt-d":
        ns += [f'xmlns:ebuttm="{NS_EBUTTM}"', f'xmlns:ebutts="{NS_EBUTTS}"']
        root_attrs.append('ttp:cellResolution="32 15"')
    else:
        root_attrs.append(f'ttp:contentProfiles="{IMSC11_TEXT_PROFILE}"')

    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<tt {" ".join(ns)} {" ".join(root_attrs)}>', "  <head>"]
    out.append("    <metadata>")
    out.append(f"      <ttm:title>{escape(document.id)}</ttm:title>")
    if profile == "ebu-tt-d":
        out.append("      <ebuttm:documentMetadata>")
        out.append(f"        <ebuttm:conformsToStandard>{EBU_TT_D_STANDARD}</ebuttm:conformsToStandard>")
        out.append("      </ebuttm:documentMetadata>")
    out.append("    </metadata>")
    out.append("    <styling>")
    out.append(
        '      <style xml:id="s_base" tts:fontFamily="proportionalSansSerif" tts:fontSize="100%" '
        'tts:lineHeight="normal" tts:color="#ffffff" tts:backgroundColor="#000000c2"/>'
    )
    for align, style_id in sorted(styles.items(), key=lambda kv: kv[1]) or [("center", "s_center")]:
        out.append(f'      <style xml:id="{style_id}" tts:textAlign="{align}"/>')
    for key, style_id in sorted(span_styles.items(), key=lambda kv: int(kv[1][len("s_span"):])):
        out.append(f'      <style xml:id="{style_id}" {_span_style_attrs(key)}/>')
    out.append("    </styling>")
    out.append("    <layout>")
    for geometry, region_id in sorted(regions.items(), key=lambda kv: kv[1]):
        x, y, w, h, display = geometry
        out.append(
            f'      <region xml:id="{region_id}" tts:origin="{_pct(x)} {_pct(y)}" '
            f'tts:extent="{_pct(w)} {_pct(h)}" tts:displayAlign="{display}" tts:overflow="visible"/>'
        )
    out.append("    </layout>")
    out.append("  </head>")
    out.append("  <body>")
    out.append("    <div>")
    out.extend(rows)
    out.append("    </div>")
    out.append("  </body>")
    out.append("</tt>")
    return "\n".join(out) + "\n"


def generate_imsc(document: SubtitleDocument) -> str:
    return generate_ttml(document, profile="ttml")


def generate_ebu_tt_d(document: SubtitleDocument) -> str:
    return generate_ttml(document, profile="ebu-tt-d")


# ---------------------------------------------------------------- validation

_ALLOWED_TT = {"tt", "head", "metadata", "styling", "style", "layout", "region", "body", "div", "p", "span", "br"}


def _q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def validate_ttml(text: str, *, profile: str) -> list:
    """Structural checks for the profile; returns the problems (empty = valid)."""
    problems = []
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        return [f"not well-formed XML: {exc}"]
    if root.tag != _q(NS_TT, "tt"):
        return ["root element is not tt in the TTML namespace"]
    if not root.get(_q(NS_XML, "lang")):
        problems.append("tt has no xml:lang")
    if root.get(_q(NS_TTP, "timeBase")) != "media":
        problems.append('tt must have ttp:timeBase="media"')
    if root.get(_q(NS_TTS, "extent")) is not None:
        problems.append("tt must not carry tts:extent (regions are in percent)")
    if profile == "ttml" and IMSC11_TEXT_PROFILE not in (root.get(_q(NS_TTP, "contentProfiles")) or "").split():
        problems.append("tt does not name the IMSC 1.1 Text profile in ttp:contentProfiles")

    head = root.find(_q(NS_TT, "head"))
    body = root.find(_q(NS_TT, "body"))
    if head is None:
        problems.append("no head")
        return problems
    styling, layout = head.find(_q(NS_TT, "styling")), head.find(_q(NS_TT, "layout"))
    if profile == "ebu-tt-d" and (styling is None or layout is None):
        problems.append("EBU-TT-D head needs styling and layout")
    if profile == "ebu-tt-d":
        conforms = root.findall(f".//{_q(NS_EBUTTM, 'conformsToStandard')}")
        if not any((c.text or "").strip() == EBU_TT_D_STANDARD for c in conforms):
            problems.append(f"EBU-TT-D document does not declare {EBU_TT_D_STANDARD}")

    ids = {}
    for el in root.iter():
        xml_id = el.get(_q(NS_XML, "id"))
        if xml_id is not None:
            if xml_id in ids:
                problems.append(f"duplicate xml:id {xml_id!r}")
            ids[xml_id] = el
        if el.tag.startswith("{" + NS_TT + "}") and el.tag.split("}")[1] not in _ALLOWED_TT:
            problems.append(f"element {el.tag.split('}')[1]!r} is not allowed")

    style_ids = {s.get(_q(NS_XML, "id")) for s in (styling.findall(_q(NS_TT, "style")) if styling is not None else [])}
    region_ids = set()
    for region in layout.findall(_q(NS_TT, "region")) if layout is not None else []:
        rid = region.get(_q(NS_XML, "id"))
        region_ids.add(rid)
        origin, extent = region.get(_q(NS_TTS, "origin")), region.get(_q(NS_TTS, "extent"))
        if origin is None or extent is None:
            problems.append(f"region {rid!r} needs tts:origin and tts:extent")
            continue
        mo, me = _PERCENT_PAIR.match(origin), _PERCENT_PAIR.match(extent)
        if not (mo and me):
            problems.append(f"region {rid!r}: origin/extent must be percentages")
            continue
        ox, oy = float(mo.group(1)), float(mo.group(2))
        ew, eh = float(me.group(1)), float(me.group(2))
        if ox + ew > 100.0001 or oy + eh > 100.0001 or ew <= 0 or eh <= 0:
            problems.append(f"region {rid!r} lies outside the root container")

    if body is None:
        return problems
    for p in body.iter(_q(NS_TT, "p")):
        pid = p.get(_q(NS_XML, "id"))
        if not pid:
            problems.append("a p has no xml:id")
        try:
            begin, end = _parse_clock(p.get("begin", "")), _parse_clock(p.get("end", ""))
            if end <= begin:
                problems.append(f"p {pid!r}: end is not after begin")
        except SubtitleSkillError as exc:
            problems.append(f"p {pid!r}: {exc.message}")
        if p.get("region") not in region_ids:
            problems.append(f"p {pid!r}: region {p.get('region')!r} is not defined")
        for sid in (p.get("style") or "").split():
            if sid not in style_ids:
                problems.append(f"p {pid!r}: style {sid!r} is not defined")
        for span in p.iter(_q(NS_TT, "span")):
            for sid in (span.get("style") or "").split():
                if sid not in style_ids:
                    problems.append(f"p {pid!r}: span style {sid!r} is not defined")
        if profile == "ebu-tt-d":
            for el in [p, *p.iter(_q(NS_TT, "span"))]:
                if any(k.startswith("{" + NS_TTS + "}") for k in el.attrib):
                    problems.append(f"p {pid!r}: EBU-TT-D allows no inline tts: styling on p or span")
            for span in p.iter(_q(NS_TT, "span")):
                if any(child.tag != _q(NS_TT, "br") for child in span):
                    problems.append(f"p {pid!r}: an EBU-TT-D span may hold only text and br")
    return problems


def check_ttml(text: str, *, profile: str) -> None:
    problems = validate_ttml(text, profile=profile)
    if problems:
        raise SubtitleSkillError(
            "OUTPUT_ERROR", f"the written {profile} document does not conform: {'; '.join(problems[:5])}"
        )


# ------------------------------------------------------------------- reading


def _text_of(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if child.tag == _q(NS_TT, "br"):
            parts.append("\n")
        elif child.tag == _q(NS_TT, "span"):
            parts.append(_text_of(child))
        parts.append(child.tail or "")
    return "".join(parts)


def parse_ttml(text: str, *, document_id: str = "ttml") -> SubtitleDocument:
    """Read cues back from an IMSC 1.1 / EBU-TT-D document written by this module
    (or any TTML with clock times and percent regions). Speaker prefixes stay in
    the text, as they do in SRT and WebVTT."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise SubtitleSkillError("INVALID_INPUT", f"not well-formed TTML: {exc}") from None
    language = root.get(_q(NS_XML, "lang")) or "und"
    regions = {}
    for region in root.iter(_q(NS_TT, "region")):
        mo = _PERCENT_PAIR.match(region.get(_q(NS_TTS, "origin")) or "")
        me = _PERCENT_PAIR.match(region.get(_q(NS_TTS, "extent")) or "")
        if mo and me:
            regions[region.get(_q(NS_XML, "id"))] = (
                float(mo.group(1)), float(mo.group(2)), float(me.group(1)), float(me.group(2)),
                region.get(_q(NS_TTS, "displayAlign")) or "before",
            )
    named = {s.get(_q(NS_XML, "id")): s for s in root.iter(_q(NS_TT, "style"))}
    aligns = {sid: el.get(_q(NS_TTS, "textAlign")) for sid, el in named.items()}

    def styled(el: ET.Element, attr: str) -> Optional[str]:
        if el.get(_q(NS_TTS, attr)) is not None:
            return el.get(_q(NS_TTS, attr))
        for sid in (el.get("style") or "").split():
            if sid in named and named[sid].get(_q(NS_TTS, attr)) is not None:
                return named[sid].get(_q(NS_TTS, attr))
        return None

    cues = []
    for index, p in enumerate(root.iter(_q(NS_TT, "p")), start=1):
        style = {}
        geometry = regions.get(p.get("region"))
        if geometry is not None and geometry != _region_for(None):
            x, y, w, h, display = geometry
            if display == "before":
                style["line"] = y
            if (x, w) != (_DEFAULT_ORIGIN[0], _DEFAULT_EXTENT[0]):
                style["size"] = w
                style["position"] = round(x + w / 2.0, 3)
        for sid in (p.get("style") or "").split():
            align = aligns.get(sid)
            if align in ("left", "right"):
                style["align"] = align
        span = p.find(_q(NS_TT, "span"))
        if span is not None:
            if styled(span, "fontWeight") == "bold":
                style["bold"] = True
            if styled(span, "fontStyle") == "italic":
                style["italic"] = True
            if styled(span, "color") is not None:
                style["color"] = styled(span, "color").lstrip("#").upper()[:6]
        cue = {
            "id": decode_id(p.get(_q(NS_XML, "id")) or str(index)),
            "start": _parse_clock(p.get("begin", "")),
            "end": _parse_clock(p.get("end", "")),
            "text": _text_of(p).strip("\n"),
        }
        if style:
            cue["style"] = style
        cues.append(cue)
    return SubtitleDocument.from_dict({"id": document_id, "language": language, "cues": cues})
