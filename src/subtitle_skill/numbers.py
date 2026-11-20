"""Spoken numbers written as one token (docs/adr/ADR-013-caption-quality.md, part 2).

`spoken_numbers: "en"` (opt-in, `generate` and `render`) rewrites number phrases as a
reader expects to see them on screen:

- amounts: "five thousand dollars" -> `$5,000`, "two point five million dollars" ->
  `$2.5 million`, "forty euros" -> `€40`;
- percentages: "forty percent" / "40 per cent" -> `40%`;
- years: "nineteen ninety-nine" -> `1999`, "twenty twenty-four" -> `2024`, and
  "two thousand and eight" after a year word ("in", "since", ...) -> `2008`;
- dates: "May fifth" -> `May 5`, "March 3rd, twenty twenty-four" -> `March 3, 2024`;
- other spelled cardinals of ten and more ("twenty-five people" -> `25 people`) and decimals
  ("three point five" -> `3.5`). One to nine stay words, as most style guides write them.

Each written form is one token: a space inside it (`$2.5 million`, `May 5, 2024`) is a
no-break space (U+00A0), so a line is never broken inside it, and the cue's word timings
are merged into one word for it, so a cue is never split inside it either (the shorts
chunker and the cut-aware split work on words). Digits already written stay as they are;
only a following unit word is folded in ("40 percent" -> `40%`).

English only today. The parser is a fixed rule set, not a model: a phrase it does not
recognise is left exactly as written. Each replacement is reported.
"""
from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple

from .errors import SubtitleSkillError
from .models import SubtitleDocument, WordTiming

LOCALES = ("en",)
NBSP = " "

_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9, "trillion": 10 ** 12}
_ORDINAL_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
                  "eighth": 8, "ninth": 9}
_ORDINAL_TEENS = {"tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
                  "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19}
_ORDINAL_TENS = {"twentieth": 20, "thirtieth": 30}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_YEAR_CONTEXT = frozenset({"in", "since", "by", "until", "till", "from", "through", "before", "after", "year",
                           "of", "circa"})
_CURRENCY = {"dollar": "$", "dollars": "$", "bucks": "$", "euro": "€", "euros": "€"}
_DIGITS = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_DIGIT_ORDINAL = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)$")
_LEAD = re.compile(r"^[\"'(\[“‘]+")
_TRAIL = re.compile(r"[\"')\].,!?;:”’…]+$")
#: what separates tokens; a no-break space does not, so a written form stays one token
_SPACE = re.compile(r"[ \t\n]+")


class _Token:
    __slots__ = ("raw", "lead", "core", "trail", "low")

    def __init__(self, raw: str):
        self.raw = raw
        lead = _LEAD.match(raw)
        self.lead = lead.group(0) if lead else ""
        rest = raw[len(self.lead):]
        trail = _TRAIL.search(rest)
        self.trail = trail.group(0) if trail else ""
        self.core = rest[: len(rest) - len(self.trail)] if self.trail else rest
        self.low = self.core.lower()


def _words_of(token: _Token) -> Optional[List[str]]:
    """A spelled number token as its number words ("forty-two" -> forty, two), or None."""
    parts = token.low.split("-") if "-" in token.low else [token.low]
    if len(parts) == 2 and parts[0] in _TENS and (parts[1] in _UNITS or parts[1] in _ORDINAL_UNITS) \
            and parts[1] not in ("zero", "oh"):
        return parts
    if len(parts) == 1 and (parts[0] in _UNITS or parts[0] in _TEENS or parts[0] in _TENS or parts[0] in _SCALES
                            or parts[0] == "hundred"):
        return parts
    return None


class _Parse:
    """Result of reading a number at a token position."""

    def __init__(self, value: Decimal, end: int, *, spelled: bool, year: bool = False,
                 scale: Optional[str] = None, has_thousand_word: bool = False):
        self.value = value
        self.end = end  # exclusive token index
        self.spelled = spelled
        self.year = year
        self.scale = scale  # trailing scale word kept in the written form ("million")
        self.has_thousand_word = has_thousand_word


def _two_digit(tokens: Sequence[_Token], i: int) -> Optional[Tuple[int, int]]:
    """10..99 spelled at i (teen, tens, tens-unit, "tens unit"), as (value, next index)."""
    if i >= len(tokens):
        return None
    words = _words_of(tokens[i])
    if not words:
        return None
    if len(words) == 2:
        return _TENS[words[0]] + _UNITS[words[1]], i + 1
    w = words[0]
    if w in _TEENS:
        return _TEENS[w], i + 1
    if w in _TENS:
        if not tokens[i].trail and i + 1 < len(tokens):
            nxt = _words_of(tokens[i + 1])
            if nxt and len(nxt) == 1 and nxt[0] in _UNITS and nxt[0] not in ("zero", "oh"):
                return _TENS[w] + _UNITS[nxt[0]], i + 2
        return _TENS[w], i + 1
    return None


def _year_form(tokens: Sequence[_Token], i: int) -> Optional[_Parse]:
    """"nineteen ninety-nine", "twenty twenty-four", "twenty oh five" ("twelve hundred" is a
    cardinal, 1,200)."""
    first = _words_of(tokens[i])
    if not first or len(first) != 1 or tokens[i].trail:
        return None
    w = first[0]
    if not ((w in _TEENS and _TEENS[w] >= 11) or w == "twenty"):
        return None
    century = _TEENS.get(w, 20)
    j = i + 1
    if j >= len(tokens):
        return None
    nxt = _words_of(tokens[j])
    if not nxt:
        return None
    if nxt == ["oh"] and not tokens[j].trail and j + 1 < len(tokens):
        unit = _words_of(tokens[j + 1])
        if unit and len(unit) == 1 and unit[0] in _UNITS and unit[0] not in ("zero", "oh"):
            return _Parse(Decimal(century * 100 + _UNITS[unit[0]]), j + 2, spelled=True, year=True)
        return None
    two = _two_digit(tokens, j)
    if two is None:
        return None
    value, end = two
    year = century * 100 + value
    if not 1100 <= year <= 2099:
        return None
    return _Parse(Decimal(year), end, spelled=True, year=True)


def _cardinal(tokens: Sequence[_Token], i: int) -> Optional[_Parse]:
    """A spelled cardinal (with "hundred", scales, "and", "a hundred") and an optional decimal."""
    total = 0
    current = 0
    last = None  # unit | teen | tens | hundred | scale
    last_scale = None
    j = i
    consumed_any = False
    has_thousand_word = False
    while j < len(tokens):
        tok = tokens[j]
        low = tok.low
        if low == "a" and not consumed_any and j + 1 < len(tokens) and _words_of(tokens[j + 1]) in (
                ["hundred"], ["thousand"], ["million"], ["billion"], ["trillion"]) and not tok.trail:
            current = 1
            last = "unit"
            j += 1
            continue
        if low == "and" and consumed_any and last in ("hundred", "scale") and not tok.trail and j + 1 < len(tokens):
            nxt = _words_of(tokens[j + 1])
            if nxt and (nxt[0] in _UNITS or nxt[0] in _TEENS or nxt[0] in _TENS):
                j += 1
                continue
            break
        words = _words_of(tok)
        if not words:
            break
        stop = False
        for w in words:
            if w in _UNITS and w not in ("oh",):
                if last in ("unit", "teen") or (last == "tens" and len(words) == 1 and w == "zero"):
                    stop = True
                    break
                if last == "tens" and current % 10 != 0:
                    stop = True
                    break
                current += _UNITS[w]
                last = "unit"
            elif w in _ORDINAL_UNITS:
                stop = True
                break
            elif w in _TEENS:
                if last in ("unit", "teen", "tens"):
                    stop = True
                    break
                current += _TEENS[w]
                last = "teen"
            elif w in _TENS:
                if last in ("unit", "teen", "tens"):
                    stop = True
                    break
                current += _TENS[w]
                last = "tens"
            elif w == "hundred":
                if last not in ("unit", "teen") or current >= 100:
                    stop = True
                    break
                current *= 100
                last = "hundred"
            elif w in _SCALES:
                scale = _SCALES[w]
                if last is None or last == "scale" or (last_scale is not None and scale >= last_scale):
                    stop = True
                    break
                total += current * scale
                current = 0
                last = "scale"
                last_scale = scale
                has_thousand_word = has_thousand_word or w == "thousand"
            else:
                stop = True
                break
        if stop:
            break
        consumed_any = True
        j += 1
        if tok.trail:
            break
    if not consumed_any or last is None:
        return None
    value = Decimal(total + current)
    # "two point five": a decimal fraction read digit by digit
    if j < len(tokens) and tokens[j].low == "point" and not tokens[j - 1].trail and not tokens[j].trail \
            and last != "scale":
        k = j + 1
        digits = ""
        while k < len(tokens):
            w = _words_of(tokens[k])
            if not w or len(w) != 1 or w[0] not in _UNITS:
                break
            digits += str(_UNITS[w[0]])
            k += 1
            if tokens[k - 1].trail:
                break
        if digits:
            value = value + Decimal("0." + digits)
            j = k
    return _Parse(value, j, spelled=True, has_thousand_word=has_thousand_word)


def _digits(tokens: Sequence[_Token], i: int) -> Optional[_Parse]:
    tok = tokens[i]
    if not _DIGITS.match(tok.core):
        return None
    return _Parse(Decimal(tok.core.replace(",", "")), i + 1, spelled=False)


def _number(tokens: Sequence[_Token], i: int) -> Optional[_Parse]:
    parsed = _digits(tokens, i)
    if parsed is None:
        parsed = _year_form(tokens, i) or _cardinal(tokens, i)
    if parsed is None:
        return None
    # "5 million", "two point five million": a scale word kept in the written form
    j = parsed.end
    if not parsed.year and j < len(tokens) and not tokens[j - 1].trail and tokens[j].low in ("million", "billion",
                                                                                             "trillion"):
        if not parsed.spelled or parsed.value < 1000:
            parsed.scale = tokens[j].low
            parsed.end = j + 1
    return parsed


def _group(value: Decimal) -> str:
    if value == value.to_integral_value():
        return f"{int(value):,}"
    whole = int(value)
    frac = format(value - whole, "f")[1:].rstrip("0")
    return f"{whole:,}{frac}"


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _magnitude(value: Decimal, scale: Optional[str]) -> str:
    if scale is not None:
        return f"{_plain(value)}{NBSP}{scale}"
    for name in ("trillion", "billion", "million"):
        size = _SCALES[name]
        if value >= size and (value * 10) % size == 0:
            return f"{_plain(value / size)}{NBSP}{name}"
    return _group(value)


def _ordinal_day(tokens: Sequence[_Token], i: int) -> Optional[Tuple[int, int]]:
    tok = tokens[i]
    m = _DIGIT_ORDINAL.match(tok.core)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1)), i + 1
    if tok.core.isdigit() and 1 <= int(tok.core) <= 31:
        return int(tok.core), i + 1
    parts = tok.low.split("-")
    if len(parts) == 1:
        w = parts[0]
        for table in (_ORDINAL_UNITS, _ORDINAL_TEENS, _ORDINAL_TENS):
            if w in table:
                return table[w], i + 1
        if w in ("twenty", "thirty") and not tok.trail and i + 1 < len(tokens):
            nxt = tokens[i + 1].low
            if nxt in _ORDINAL_UNITS and _TENS[w] + _ORDINAL_UNITS[nxt] <= 31:
                return _TENS[w] + _ORDINAL_UNITS[nxt], i + 2
    elif len(parts) == 2 and parts[0] in ("twenty", "thirty") and parts[1] in _ORDINAL_UNITS:
        day = _TENS[parts[0]] + _ORDINAL_UNITS[parts[1]]
        if day <= 31:
            return day, i + 1
    return None


def _date(tokens: Sequence[_Token], i: int) -> Optional[Tuple[str, int]]:
    tok = tokens[i]
    if tok.core not in _MONTHS or tok.trail or tok.lead or i + 1 >= len(tokens):
        return None
    day = _ordinal_day(tokens, i + 1)
    if day is None:
        return None
    value, end = day
    written = f"{tok.core}{NBSP}{value}"
    # ", twenty twenty-four" / ", 2024"
    if tokens[end - 1].trail == "," and end < len(tokens):
        year = _year_form(tokens, end) or _digits(tokens, end)
        if year is not None and 1000 <= year.value <= 2099 and year.value == year.value.to_integral_value():
            return f"{written},{NBSP}{int(year.value)}", year.end
    return written, end


def _phrase_at(tokens: Sequence[_Token], i: int) -> Optional[Tuple[str, int]]:
    """The written form of the phrase starting at token i, and the index after it."""
    date = _date(tokens, i)
    if date is not None:
        return date
    parsed = _number(tokens, i)
    if parsed is None:
        return None
    j = parsed.end
    unit_tok = tokens[j] if j < len(tokens) and not tokens[j - 1].trail else None
    unit = unit_tok.low if unit_tok is not None else None
    # "per cent"
    if unit == "per" and j + 1 < len(tokens) and tokens[j + 1].low == "cent" and not unit_tok.trail:
        return f"{_magnitude(parsed.value, parsed.scale) if parsed.scale else _group(parsed.value)}%", j + 2
    if unit == "percent":
        number = _group(parsed.value) if parsed.scale is None else _magnitude(parsed.value, parsed.scale)
        return f"{number}%", j + 1
    if unit in _CURRENCY and not parsed.year:
        return f"{_CURRENCY[unit]}{_magnitude(parsed.value, parsed.scale)}", j + 1
    if not parsed.spelled:
        if parsed.scale is not None:
            return _magnitude(parsed.value, parsed.scale), j
        return None  # digits already written: nothing to do
    if parsed.year:
        return str(int(parsed.value)), j
    previous = tokens[i - 1].low if i > 0 else ""
    if parsed.has_thousand_word and 1000 <= parsed.value <= 2099 and previous in _YEAR_CONTEXT \
            and parsed.value == parsed.value.to_integral_value():
        return str(int(parsed.value)), j
    if parsed.value != parsed.value.to_integral_value():
        return _magnitude(parsed.value, parsed.scale), j
    if parsed.value < 10 and parsed.scale is None:
        return None  # one to nine stay words
    return _magnitude(parsed.value, parsed.scale), j


def rewrite_tokens(raw_tokens: Sequence[str]) -> List[Tuple[int, int, str]]:
    """Every phrase found in `raw_tokens`, as (first index, end index, written form with the
    first token's leading and the last token's trailing punctuation kept)."""
    tokens = [_Token(t) for t in raw_tokens]
    found = []
    i = 0
    while i < len(tokens):
        hit = _phrase_at(tokens, i)
        if hit is None:
            i += 1
            continue
        written, end = hit
        found.append((i, end, tokens[i].lead + written + tokens[end - 1].trail))
        i = end
    return found


def rewrite_text(text: str) -> Tuple[str, List[dict]]:
    """`text` with each phrase written; separators outside a phrase are kept as they were.
    A line break inside a phrase moves to after it."""
    pieces = _SPACE.split(text)
    seps = _SPACE.findall(text)
    lead_space = ""
    if pieces and pieces[0] == "":
        lead_space = seps.pop(0) if seps else ""
        pieces = pieces[1:]
    trail_space = ""
    if pieces and pieces[-1] == "":
        trail_space = seps.pop() if seps else ""
        pieces = pieces[:-1]
    found = rewrite_tokens(pieces)
    if not found:
        return text, []
    out = lead_space
    i = 0
    changes = []
    for start, end, written in found:
        while i < start:
            out += pieces[i] + (seps[i] if i < len(seps) else "")
            i += 1
        inner = seps[start:end - 1]
        after = seps[end - 1] if end - 1 < len(seps) else ""
        if any("\n" in s for s in inner) and "\n" not in after:
            after = "\n" if after else after
        out += written + after
        changes.append({"spoken": " ".join(pieces[start:end]), "written": written})
        i = end
    while i < len(pieces):
        out += pieces[i] + (seps[i] if i < len(seps) else "")
        i += 1
    return out + trail_space, changes


def rewrite_words(words: Sequence[WordTiming]) -> tuple:
    """Word timings with each phrase merged into one word (first start, last end)."""
    found = rewrite_tokens([w.text for w in words])
    if not found:
        return tuple(words)
    out = []
    i = 0
    for start, end, written in found:
        out.extend(words[i:start])
        out.append(WordTiming(written, words[start].start, words[end - 1].end))
        i = end
    out.extend(words[i:])
    return tuple(out)


def parse_locale(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if raw is True:
        return "en"
    if raw not in LOCALES:
        raise SubtitleSkillError(
            "INVALID_INPUT", f"spoken_numbers must be one of {list(LOCALES)} (or true for 'en'), got {raw!r}",
            details={"field": "spoken_numbers"},
        )
    return raw


def normalize_document(document: SubtitleDocument, locale: str) -> Tuple[SubtitleDocument, dict]:
    """The document with spoken numbers written, and the report of what changed."""
    replacements = []
    cues = []
    for cue in document.cues:
        text, changes = rewrite_text(cue.text)
        if cue.ruby:
            # a ruby base must stay findable in the text: leave a cue with ruby as it is
            text, changes = cue.text, []
        words = rewrite_words(cue.words) if cue.words and changes else cue.words
        replacements.extend({"cue_id": cue.id, **c} for c in changes)
        cues.append(replace(cue, text=text, words=words) if changes else cue)
    report = {"locale": locale, "replacements": replacements}
    return replace(document, cues=tuple(cues)), report


def has_spaced_token(report: dict) -> bool:
    """Whether any written form holds a no-break space (only an ASS burn keeps it whole:
    ffmpeg-skill caption re-wraps an SRT and treats U+00A0 as a space)."""
    return any(NBSP in r["written"] for r in report.get("replacements", ()))
