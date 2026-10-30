"""Locale profiles: per-language character counting, kinsoku line-break
rules and default line-length / reading-speed thresholds.

A profile is selected by the primary subtag of `SubtitleDocument.language`
(`ja`, `ja-JP`, `zh-Hans`, `zh-TW`, `ko-KR`, ...). Languages without a
profile keep the generic behavior unchanged: `len()` counting and the
generic defaults in `validation.py`.

Every number and character class here is taken from a published source;
docs/adr/ADR-001-locale-profiles.md records which source backs which value,
and which values could not be verified and are conservative choices instead.

Nothing in this module changes subtitle text on its own. `break_lines` is a
deterministic helper whose result `validation.py` only *reports* as a
suggestion; it is never applied to a cue.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Tuple


def _chars(codepoints: Iterable[int]) -> FrozenSet[str]:
    return frozenset(chr(cp) for cp in codepoints)


def _with_compat_forms(base: FrozenSet[str]) -> FrozenSet[str]:
    """Add the halfwidth/fullwidth compatibility forms (U+FF01..U+FFEE) whose
    NFKC normalization is a member of `base`.

    JLREQ lists e.g. U+0028 LEFT PARENTHESIS in cl-01 and notes that Japanese
    text normally uses its compatibility counterpart U+FF08 instead; this
    applies the same rule to every class (fullwidth ASCII punctuation and the
    halfwidth katakana forms such as U+FF61/U+FF63/U+FF70/U+FF6F).
    """
    extra = set()
    for cp in range(0xFF01, 0xFFEF):
        ch = chr(cp)
        if unicodedata.normalize("NFKC", ch) in base:
            extra.add(ch)
    return frozenset(base | extra)


# W3C "Requirements for Japanese Text Layout" (JLREQ, W3C Working Group Note
# 2020-08-11), Appendix A, character classes -- the public description of
# JIS X 4051:2004's classes. Code points copied from the appendix tables.
JLREQ_CL01_OPENING_BRACKETS = _chars(
    [0x2018, 0x201C, 0x0028, 0x3014, 0x005B, 0x007B, 0x3008, 0x300A, 0x300C, 0x300E, 0x3010, 0x2985, 0x3018, 0x3016, 0x00AB, 0x301D]
)
JLREQ_CL02_CLOSING_BRACKETS = _chars(
    [0x2019, 0x201D, 0x0029, 0x3015, 0x005D, 0x007D, 0x3009, 0x300B, 0x300D, 0x300F, 0x3011, 0x2986, 0x3019, 0x3017, 0x00BB, 0x301F]
)
JLREQ_CL03_HYPHENS = _chars([0x2010, 0x301C, 0x30A0, 0x2013])
JLREQ_CL04_DIVIDING_PUNCTUATION = _chars([0x0021, 0x003F, 0x203C, 0x2047, 0x2048, 0x2049])
JLREQ_CL05_MIDDLE_DOTS = _chars([0x30FB, 0x003A, 0x003B])
JLREQ_CL06_FULL_STOPS = _chars([0x3002, 0x002E])
JLREQ_CL07_COMMAS = _chars([0x3001, 0x002C])
JLREQ_CL09_ITERATION_MARKS = _chars([0x30FD, 0x30FE, 0x309D, 0x309E, 0x3005, 0x303B])
JLREQ_CL10_PROLONGED_SOUND_MARK = _chars([0x30FC])
# cl-11 also lists the sequence U+31F7 U+309A; U+31F7 alone is already a member.
JLREQ_CL11_SMALL_KANA = _chars(
    [0x3041, 0x3043, 0x3045, 0x3047, 0x3049, 0x30A1, 0x30A3, 0x30A5, 0x30A7, 0x30A9, 0x3063, 0x3083, 0x3085, 0x3087,
     0x308E, 0x3095, 0x3096, 0x30C3, 0x30E3, 0x30E5, 0x30E7, 0x30EE, 0x30F5, 0x30F6]
    + list(range(0x31F0, 0x3200))
)

# JLREQ 3.1.7 (line-start prohibition, strict form: the relaxations its Note
# allows for 々, ー and small kana are not applied) and 3.1.8 (line-end).
JA_NO_LINE_START = _with_compat_forms(
    JLREQ_CL02_CLOSING_BRACKETS
    | JLREQ_CL03_HYPHENS
    | JLREQ_CL04_DIVIDING_PUNCTUATION
    | JLREQ_CL05_MIDDLE_DOTS
    | JLREQ_CL06_FULL_STOPS
    | JLREQ_CL07_COMMAS
    | JLREQ_CL09_ITERATION_MARKS
    | JLREQ_CL10_PROLONGED_SOUND_MARK
    | JLREQ_CL11_SMALL_KANA
)
JA_NO_LINE_END = _with_compat_forms(JLREQ_CL01_OPENING_BRACKETS)

# W3C "Requirements for Chinese Text Layout" (CLREQ) 6.1.1, the "basic" rule
# set ("the most recommended method"): pause/stop marks, closing quotation
# marks/brackets/book-title marks, interpuncts and solidi (分隔号, named in the
# Chinese text of the rule) do not start a line; opening quotation marks,
# brackets and book-title marks do not end one. Connector marks (连接号) are
# also named, but are not included here -- see the ADR.
ZH_PAUSE_STOP_MARKS = frozenset("、，。：；！？")
ZH_INTERPUNCTS = _chars([0x00B7, 0x30FB])  # CLREQ: U+00B7 MIDDLE DOT; U+30FB as used in JLREQ cl-05
ZH_SOLIDI = _chars([0x002F, 0xFF0F])  # CLREQ: U+002F SOLIDUS, U+FF0F FULLWIDTH SOLIDUS
ZH_NO_LINE_START = _with_compat_forms(ZH_PAUSE_STOP_MARKS | JLREQ_CL02_CLOSING_BRACKETS | ZH_INTERPUNCTS | ZH_SOLIDI)
ZH_NO_LINE_END = _with_compat_forms(JLREQ_CL01_OPENING_BRACKETS)

# W3C "Requirements for Hangul Text Layout and Typography" (KLREQ) 7.1.2 /
# 7.1.3: closing parentheses (cl2), hyphens (cl5), dividing punctuation
# (cl6), middle dots (cl7), periods and commas (cl8~9), iteration marks
# (cl11) and the prolonged sound mark (cl12) do not start a line; opening
# parentheses (cl1) do not end one. Represented by the JLREQ class of the
# same name (see the ADR).
KO_NO_LINE_START = _with_compat_forms(
    JLREQ_CL02_CLOSING_BRACKETS
    | JLREQ_CL03_HYPHENS
    | JLREQ_CL04_DIVIDING_PUNCTUATION
    | JLREQ_CL05_MIDDLE_DOTS
    | JLREQ_CL06_FULL_STOPS
    | JLREQ_CL07_COMMAS
    | JLREQ_CL09_ITERATION_MARKS
    | JLREQ_CL10_PROLONGED_SOUND_MARK
)
KO_NO_LINE_END = _with_compat_forms(JLREQ_CL01_OPENING_BRACKETS)


@dataclass(frozen=True)
class LocaleProfile:
    """Per-language defaults and counting rules. See ADR-001 for sources."""

    language: str
    #: default line length, in full-width character units
    max_chars_per_line: int
    #: default reading speed, in the same units per second
    reading_speed_cps: float
    #: weight of a narrow / halfwidth / neutral character (East Asian Width
    #: Na, H, N); wide, fullwidth and ambiguous characters weigh 1
    half_width_weight: float
    no_line_start: FrozenSet[str]
    no_line_end: FrozenSet[str]
    #: suggest breaks at spaces (word, 어절) before breaking inside a word
    break_at_spaces: bool = False

    def char_width(self, ch: str) -> float:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            return 0.0  # combining marks, zero-width joiners: no advance of their own
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ("W", "F", "A"):
            # UAX #11 section 5: ambiguous characters resolve to wide when the
            # language tag gives an East Asian context, which a profile always is.
            return 1.0
        return self.half_width_weight

    def text_width(self, text: str) -> float:
        return sum(self.char_width(ch) for ch in text if ch != "\n")

    def line_start_violation(self, line: str) -> Optional[str]:
        stripped = line.lstrip(" 　")
        if stripped and stripped[0] in self.no_line_start:
            return stripped[0]
        return None

    def line_end_violation(self, line: str) -> Optional[str]:
        stripped = line.rstrip(" 　")
        if stripped and stripped[-1] in self.no_line_end:
            return stripped[-1]
        return None


PROFILES = {
    # Netflix Japanese Timed Text Style Guide: 13 full-width characters per
    # (horizontal) line; half-width characters count as 0.5; up to 4
    # characters per second.
    "ja": LocaleProfile(
        language="ja",
        max_chars_per_line=13,
        reading_speed_cps=4.0,
        half_width_weight=0.5,
        no_line_start=JA_NO_LINE_START,
        no_line_end=JA_NO_LINE_END,
    ),
    # Netflix Chinese (Simplified) and Chinese (Traditional) Timed Text Style
    # Guides: 16 characters per line, up to 9 characters per second (adult).
    # Neither guide states how half-width characters count: 1 is the
    # conservative (stricter) choice -- see the ADR.
    "zh": LocaleProfile(
        language="zh",
        max_chars_per_line=16,
        reading_speed_cps=9.0,
        half_width_weight=1.0,
        no_line_start=ZH_NO_LINE_START,
        no_line_end=ZH_NO_LINE_END,
    ),
    # Netflix Korean Timed Text Style Guide: 16 characters per line; Latin
    # characters, spaces and punctuation count as 0.5; up to 12 characters
    # per second (adult).
    "ko": LocaleProfile(
        language="ko",
        max_chars_per_line=16,
        reading_speed_cps=12.0,
        half_width_weight=0.5,
        no_line_start=KO_NO_LINE_START,
        no_line_end=KO_NO_LINE_END,
        break_at_spaces=True,
    ),
}


def profile_for(language: Optional[str]) -> Optional[LocaleProfile]:
    """The profile for a BCP 47 tag's primary language subtag, or None."""
    if not language:
        return None
    return PROFILES.get(language.split("-", 1)[0].lower())


# --------------------------------------------------------------------------
# Line breaking
# --------------------------------------------------------------------------

_SPACES = (" ", "　")
#: JLREQ cl-08 (inseparable characters): a run of the same one is never split
_INSEPARABLE = frozenset("—‥…―⸺⸻")


def _is_narrow_word_char(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("Na", "H", "N") and (ch.isalnum() or ch in "'-_")


def _tokenize(text: str, profile: LocaleProfile) -> List[str]:
    """Split text into units a line break may fall between.

    Spaces are their own units. A run of narrow alphanumerics (a Latin word
    or a number) is one unit, as is a run of one repeated inseparable mark
    (e.g. "……"). With `break_at_spaces` (Korean word-unit breaking, KLREQ
    7.1.1) every space-delimited word is one unit. Everything else -- kana,
    kanji, hangul, punctuation -- is one unit per character.
    """
    units: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _SPACES:
            units.append(ch)
            i += 1
            continue
        j = i + 1
        if profile.break_at_spaces:
            while j < n and text[j] not in _SPACES:
                j += 1
        elif _is_narrow_word_char(ch):
            while j < n and _is_narrow_word_char(text[j]):
                j += 1
        elif ch in _INSEPARABLE:
            while j < n and text[j] == ch:
                j += 1
        else:
            # keep combining marks with the character they modify
            while j < n and unicodedata.category(text[j]) in ("Mn", "Me"):
                j += 1
        units.append(text[i:j])
        i = j
    return units


def _join_lines(text: str, profile: LocaleProfile) -> str:
    """Undo existing line breaks before re-breaking.

    Korean breaks lines at spaces, so a removed break becomes a space. In
    Japanese and Chinese a line break carries no space, except between two
    narrow (e.g. Latin) words, where one is restored.
    """
    lines = text.split("\n")
    out = lines[0]
    for line in lines[1:]:
        if not out or not line:
            out += line
        elif profile.break_at_spaces:
            out = out.rstrip(" ") + " " + line.lstrip(" ")
        elif _is_narrow_word_char(out[-1]) and _is_narrow_word_char(line[0]):
            out += " " + line
        else:
            out += line
    return out


def _width(units: List[str], profile: LocaleProfile) -> float:
    return sum(profile.text_width(u) for u in units)


def _render(units: List[str]) -> str:
    return "".join(units).strip(" 　")


def _legal_split(head: List[str], tail_first: str, profile: LocaleProfile) -> bool:
    head_text = _render(head)
    if not head_text:
        return False
    if profile.line_end_violation(head_text) is not None:
        return False
    if profile.line_start_violation(tail_first) is not None:
        return False
    return True


def _split_wide_units(units: List[str], max_width: float, profile: LocaleProfile) -> List[str]:
    """A single unit wider than the line can only be split inside itself."""
    out: List[str] = []
    for u in units:
        if len(u) > 1 and profile.text_width(u) > max_width:
            out.extend(u)
        else:
            out.append(u)
    return out


def break_lines(text: str, profile: LocaleProfile, max_width: float) -> List[str]:
    """Break `text` into lines no wider than `max_width` (in the profile's
    full-width units), following the profile's kinsoku rules.

    Deterministic and greedy: each line takes as many units as fit; when the
    next unit does not fit, the break moves back (追い出し, pushing characters
    to the next line) until no line would start with a no-line-start
    character or end with a no-line-end one. When no legal break exists the
    line is broken at the width limit and the violation remains (the
    caller's validation will still report it). The number of lines is not
    limited here.
    """
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    units = _split_wide_units(_tokenize(_join_lines(text, profile), profile), max_width, profile)
    lines: List[str] = []
    cur: List[str] = []
    for unit in units:
        if unit in _SPACES and not cur:
            continue
        if _width(cur + [unit], profile) <= max_width or unit in _SPACES:
            cur.append(unit)
            continue
        cut = None
        for k in range(len(cur), 0, -1):
            rest = [u for u in cur[k:] + [unit] if u not in _SPACES]
            if rest and _legal_split(cur[:k], rest[0], profile):
                cut = k
                break
        if cut is None:
            cut = len(cur)
        lines.append(_render(cur[:cut]))
        cur = cur[cut:] + [unit]
        while cur and cur[0] in _SPACES:
            cur.pop(0)
        # pushing units back can, rarely, overfill the new line: hard-break it
        while _width(cur, profile) > max_width and len(cur) > 1:
            k = len(cur) - 1
            while k > 1 and _width(cur[:k], profile) > max_width:
                k -= 1
            lines.append(_render(cur[:k]))
            cur = cur[k:]
            while cur and cur[0] in _SPACES:
                cur.pop(0)
    if _render(cur):
        lines.append(_render(cur))
    return lines


def kinsoku_violations(lines: List[str], profile: LocaleProfile) -> List[Tuple[int, str, str]]:
    """(line index, "start" | "end", offending character) for every line
    break in `lines` that breaks the profile's kinsoku rules. Only positions
    next to a line break are checked: the start of every line after the
    first and the end of every line before the last."""
    found: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if i > 0:
            ch = profile.line_start_violation(line)
            if ch is not None:
                found.append((i, "start", ch))
        if i < len(lines) - 1:
            ch = profile.line_end_violation(line)
            if ch is not None:
                found.append((i, "end", ch))
    return found
