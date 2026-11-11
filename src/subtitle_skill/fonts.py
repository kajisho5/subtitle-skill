"""`request.fonts_dir`: the font family a burn is drawn with, read from the
caller's own font files (docs/adr/ADR-010-fonts-dir.md).

ffmpeg-skill's `caption --fonts-dir DIR` only tells libass where more font
files are. libass draws with a file from that directory when a style names
its family; it does not fall back to those files for a glyph the named family
lacks. Measured, not assumed: on a machine with no Japanese font,
`--fonts-dir` alone burns Japanese text as empty boxes and the run still
reports `completed`; with `--font <family>` (or an ASS style naming the
family) the same directory draws the text.

So subtitle-skill has to name a family that is in the directory and has the
glyphs. This module reads that from the font files themselves -- the `name`
and `cmap` tables of TrueType / OpenType files and collections -- with the
standard library only. It never renders, never calls fontconfig, and never
looks outside the directory it is given.
"""
from __future__ import annotations

import hashlib
import struct
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .errors import SubtitleSkillError
from .provenance import sha256_file

#: Font files read from a fonts directory (libass reads the same containers).
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")

#: Bounds on what is read from one file: a font's `name` and `cmap` tables are
#: far smaller, so anything past these is not a font worth trusting.
_MAX_FACES = 256
_MAX_TABLES = 512
_MAX_TABLE_BYTES = 32 * 1024 * 1024
_MAX_FILES = 512

_SFNT_VERSIONS = (b"\x00\x01\x00\x00", b"OTTO", b"true")
_NAME_FAMILY = 1
_PLATFORM_UNICODE = 0
_PLATFORM_MICROSOFT = 3
_LANG_EN_US = 0x0409


@dataclass(frozen=True)
class FontFace:
    """One face of one font file: its family name and the code points it maps."""

    file: str
    index: int
    family: str
    starts: tuple
    ends: tuple

    def covers(self, codepoint: int) -> bool:
        i = bisect_right(self.starts, codepoint) - 1
        return i >= 0 and codepoint <= self.ends[i]


@dataclass(frozen=True)
class FontChoice:
    """The family a burn names, and what of the text it does not have."""

    family: str
    #: the files in the directory that carry this family (libass picks among
    #: them by weight and slant)
    files: tuple
    missing: tuple


class _Unreadable(Exception):
    pass


def _read_at(handle, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0 or length > _MAX_TABLE_BYTES:
        raise _Unreadable()
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise _Unreadable()
    return data


def _tables(handle, offset: int) -> dict:
    header = _read_at(handle, offset, 12)
    if header[:4] not in _SFNT_VERSIONS:
        raise _Unreadable()
    count = struct.unpack(">H", header[4:6])[0]
    if count > _MAX_TABLES:
        raise _Unreadable()
    records = _read_at(handle, offset + 12, 16 * count)
    tables = {}
    for i in range(count):
        tag, _checksum, table_offset, length = struct.unpack(">4sIII", records[16 * i:16 * i + 16])
        tables[tag] = (table_offset, length)
    return tables


def _family(handle, tables: dict) -> Optional[str]:
    """The family libass matches a style's font name against: name id 1 in the
    Microsoft platform's records (US English first). A face without one is
    matched by a name this reader does not see, so it is not offered."""
    if b"name" not in tables:
        return None
    data = _read_at(handle, *tables[b"name"])
    if len(data) < 6:
        raise _Unreadable()
    _format, count, strings = struct.unpack(">HHH", data[:6])
    best = None
    for i in range(count):
        record = data[6 + 12 * i:18 + 12 * i]
        if len(record) != 12:
            raise _Unreadable()
        platform, _encoding, language, name_id, length, offset = struct.unpack(">HHHHHH", record)
        if name_id != _NAME_FAMILY or platform != _PLATFORM_MICROSOFT:
            continue
        raw = data[strings + offset:strings + offset + length]
        if len(raw) != length:
            raise _Unreadable()
        text = raw.decode("utf-16-be", errors="replace").strip("\x00 ")
        if not text:
            continue
        if language == _LANG_EN_US:
            return text
        if best is None:
            best = text
    return best


def _cmap_ranges(handle, tables: dict) -> list:
    if b"cmap" not in tables:
        raise _Unreadable()
    data = _read_at(handle, *tables[b"cmap"])
    if len(data) < 4:
        raise _Unreadable()
    count = struct.unpack(">H", data[2:4])[0]
    subtables = {}
    for i in range(count):
        record = data[4 + 8 * i:12 + 8 * i]
        if len(record) != 8:
            raise _Unreadable()
        platform, encoding, offset = struct.unpack(">HHI", record)
        subtables[(platform, encoding)] = offset
    # full Unicode first, then the Basic Multilingual Plane
    for key in ((_PLATFORM_MICROSOFT, 10), (_PLATFORM_UNICODE, 6), (_PLATFORM_UNICODE, 4),
                (_PLATFORM_MICROSOFT, 1), (_PLATFORM_UNICODE, 3)):
        offset = subtables.get(key)
        if offset is None or offset + 2 > len(data):
            continue
        fmt = struct.unpack(">H", data[offset:offset + 2])[0]
        if fmt == 12:
            return _format12(data, offset)
        if fmt == 4:
            return _format4(data, offset)
    raise _Unreadable()


def _format12(data: bytes, offset: int) -> list:
    if offset + 16 > len(data):
        raise _Unreadable()
    groups = struct.unpack(">I", data[offset + 12:offset + 16])[0]
    body = data[offset + 16:offset + 16 + 12 * groups]
    if len(body) != 12 * groups:
        raise _Unreadable()
    ranges = []
    for i in range(groups):
        start, end, _glyph = struct.unpack(">III", body[12 * i:12 * i + 12])
        if start <= end:
            ranges.append((start, end))
    return ranges


def _format4(data: bytes, offset: int) -> list:
    if offset + 14 > len(data):
        raise _Unreadable()
    segments = struct.unpack(">H", data[offset + 6:offset + 8])[0] // 2
    ends_at = offset + 14
    starts_at = ends_at + 2 * segments + 2
    if starts_at + 2 * segments > len(data):
        raise _Unreadable()
    ends = struct.unpack(f">{segments}H", data[ends_at:ends_at + 2 * segments])
    starts = struct.unpack(f">{segments}H", data[starts_at:starts_at + 2 * segments])
    # the closing 0xFFFF segment maps nothing
    return [(s, e) for s, e in zip(starts, ends) if s <= e and s != 0xFFFF]


def _merge(ranges: list) -> tuple:
    starts, ends = [], []
    for start, end in sorted(ranges):
        if ends and start <= ends[-1] + 1:
            ends[-1] = max(ends[-1], end)
        else:
            starts.append(start)
            ends.append(end)
    return tuple(starts), tuple(ends)


def _faces_of(path: Path) -> list:
    faces = []
    with open(path, "rb") as handle:
        head = handle.read(12)
        if head[:4] == b"ttcf":
            if len(head) != 12:
                raise _Unreadable()
            count = struct.unpack(">I", head[8:12])[0]
            if count > _MAX_FACES:
                raise _Unreadable()
            offsets = struct.unpack(f">{count}I", _read_at(handle, 12, 4 * count))
        else:
            offsets = (0,)
        for index, offset in enumerate(offsets):
            try:
                tables = _tables(handle, offset)
                family = _family(handle, tables)
                # a comma or a line break cannot be written into an ASS style's Fontname
                if not family or any(ch in family for ch in ",\r\n"):
                    continue
                starts, ends = _merge(_cmap_ranges(handle, tables))
            except (_Unreadable, struct.error):
                continue
            faces.append(FontFace(path.name, index, family, starts, ends))
    return faces


def font_files(fonts_dir: Path) -> list:
    """The font files directly inside `fonts_dir` (libass does not look into
    subdirectories either), by name."""
    try:
        entries = sorted(p for p in fonts_dir.iterdir() if p.suffix.lower() in FONT_SUFFIXES and p.is_file())
    except OSError as exc:
        raise SubtitleSkillError("INVALID_INPUT", f"fonts_dir could not be read: {exc}") from exc
    if len(entries) > _MAX_FILES:
        raise SubtitleSkillError("INVALID_INPUT", f"fonts_dir holds more than {_MAX_FILES} font files")
    return entries


def scan(fonts_dir: Path) -> list:
    """Every readable face in `fonts_dir`, in file-name then face order. A file
    that is not a font this reader understands contributes nothing."""
    faces = []
    for path in font_files(fonts_dir):
        try:
            faces += _faces_of(path)
        except (OSError, _Unreadable, struct.error):
            continue
    return faces


def digest(fonts_dir: Path) -> str:
    """One hash over the names and bytes of the font files in `fonts_dir`: the
    part of a burn's identity that changes when a font is added, removed or
    replaced. libass may draw from any of them (a bold face of the family)."""
    h = hashlib.sha256()
    for path in font_files(fonts_dir):
        h.update(path.name.encode("utf-8") + b"\x00" + sha256_file(path).encode("ascii") + b"\n")
    return h.hexdigest()


def text_characters(texts: Iterable[str]) -> list:
    """The distinct characters of `texts` a font has to draw: letters, digits
    and punctuation. Spaces, controls, combining marks and symbols (emoji among
    them, which ffmpeg-skill draws from its own assets) are not asked of it."""
    seen = {}
    for text in texts:
        for ch in text:
            if unicodedata.category(ch)[0] in ("L", "N", "P"):
                seen.setdefault(ch, None)
    return list(seen)


def choose(faces: list, characters: list, *, preferred: Optional[str] = None) -> FontChoice:
    """The face that has the most of `characters`; between equals the family
    named `preferred`, then the first by file name. Refused when the directory
    has nothing to offer for the text: a family that draws none of its
    non-ASCII characters would burn empty boxes."""
    if not faces:
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"fonts_dir holds no font file subtitle-skill can read ({', '.join(FONT_SUFFIXES)} with a family name)",
        )
    wanted = (preferred or "").casefold()
    best, best_key = None, None
    for order, face in enumerate(faces):
        covered = sum(1 for ch in characters if face.covers(ord(ch)))
        key = (covered, face.family.casefold() == wanted, -order)
        if best_key is None or key > best_key:
            best, best_key = face, key
    missing = tuple(ch for ch in characters if not best.covers(ord(ch)))
    beyond_ascii = [ch for ch in characters if ord(ch) > 0x7F]
    if beyond_ascii and all(ch in missing for ch in beyond_ascii):
        sample = "".join(beyond_ascii[:8])
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"no font in fonts_dir has the characters of this subtitle (for example {sample!r}); "
            "put a font file that covers its script in that directory",
        )
    files = tuple(sorted({face.file for face in faces if face.family == best.family}))
    return FontChoice(best.family, files, missing)
