"""SubRip (.srt) generation.

SRT has no native representation for cue position/alignment/size. If a cue
carries that style information we refuse to silently drop it -- the caller
must either omit it or choose a format that supports it (WebVTT).

Cue-level `metadata` is provenance/auxiliary data, not renderable text; it
is intentionally never written into the subtitle body of any format.
"""
from __future__ import annotations

import re
from typing import FrozenSet

from ..errors import SubtitleSkillError
from ..models import SubtitleDocument, SubtitleCue
from ._text import plain_text

_UNSUPPORTED_STYLE_FIELDS = ("align", "position", "line", "size", "color", "vertical")


def _format_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _escape_text(cue: SubtitleCue, *, allow_style_fields: FrozenSet[str] = frozenset(), language: str = "und") -> str:
    # SRT has no ruby: a reading is written in brackets after its base (formats._text)
    text = plain_text(cue, language)

    if cue.style is not None:
        unsupported = [
            f
            for f in _UNSUPPORTED_STYLE_FIELDS
            if f not in allow_style_fields and getattr(cue.style, f) is not None
        ]
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


def generate_srt(
    document: SubtitleDocument,
    *,
    allow_style_fields: FrozenSet[str] = frozenset(),
    top_cue_ids: FrozenSet[str] = frozenset(),
) -> str:
    """Render `document` as a plain .srt file.

    `allow_style_fields` is an internal escape hatch, used only by
    `operations._run_render` for `mode="burn"` (kajisho5/subtitle-skill#5)
    -- it is never set by the `generate` operation or any other caller of
    this function, which keep the full, honest rejection below. `"color"`/
    `"size"` are still meaningless inside a *bare* .srt file on their own
    (a plain .srt has no styling channel), but render's SRT is never
    handed to anyone by itself: it is immediately burned in by
    ffmpeg-skill's `caption` tool alongside its own `--color`/`--size`
    flags -- a separate, whole-burn `force_style` override that has
    nothing to do with the SRT file's own (lack of) markup -- so rejecting
    them here would block a render that will actually work. `"align"`/
    `"position"`/`"line"` never get that exemption: caption.py has no
    equivalent for those either, for either mode (see
    `engine._style_argv`'s docstring), so they stay a real
    `UNSUPPORTED_FORMAT` for `render` too, not a silently-dropped field.

    `top_cue_ids` (render's soft track with `placement`, roadmap step 8) puts
    `{\\an8}` before those cues' text: the SubRip alignment tag players and
    libass read as "top centre".
    """
    ordered = sorted(document.cues, key=lambda c: (c.start, c.end, c.id))
    blocks = []
    for index, cue in enumerate(ordered, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}",
                    ("{\\an8}" if cue.id in top_cue_ids else "")
                    + _escape_text(cue, allow_style_fields=allow_style_fields, language=document.language),
                    "",
                ]
            )
        )
    return "\n".join(blocks) + ("\n" if blocks else "")


_SRT_TIME = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)[,.](\d{1,3})$")


def _parse_srt_time(value: str) -> float:
    m = _SRT_TIME.match(value.strip())
    if not m:
        raise SubtitleSkillError("INVALID_INPUT", f"SRT time {value!r} is not hh:mm:ss,mmm")
    hours, minutes, secs, ms = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str, *, language: str, document_id: str = "srt") -> SubtitleDocument:
    """Read a SubRip file back into a document (used for format round trips).

    The block number becomes the cue id; a cue whose whole text is wrapped in
    `<b>`/`<i>` gets `style.bold`/`style.italic`. Speaker prefixes stay in the
    text: SRT has no separate speaker field."""
    cues = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.lstrip("﻿").strip())
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2:
            raise SubtitleSkillError("INVALID_INPUT", f"SRT block without a timing line: {block[:40]!r}")
        if "-->" not in lines[1]:
            raise SubtitleSkillError("INVALID_INPUT", f"SRT block {lines[0]!r} has no timing line")
        start, end = (part.strip().split(" ")[0] for part in lines[1].split("-->", 1))
        body = "\n".join(lines[2:])
        style = {}
        for tag, field in (("i", "italic"), ("b", "bold")):
            if body.startswith(f"<{tag}>") and body.endswith(f"</{tag}>"):
                body = body[len(tag) + 2:-(len(tag) + 3)]
                style[field] = True
        cue = {"id": lines[0].strip(), "start": _parse_srt_time(start), "end": _parse_srt_time(end), "text": body}
        if style:
            cue["style"] = style
        cues.append(cue)
    return SubtitleDocument.from_dict({"id": document_id, "language": language, "cues": cues})
