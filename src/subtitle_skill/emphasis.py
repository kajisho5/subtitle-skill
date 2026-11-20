"""Emphasis: one highlighted word per cue (docs/adr/ADR-013-caption-quality.md, part 3).

`render` `emphasis` (opt-in, burn only) marks the words drawn in the highlight colour and
bold. Modes:

- `off` (default): nothing is marked.
- `auto`: a fixed rule, in this order of preference: a number or amount (a token with a
  digit or a currency or percent sign), then a proper name (a capitalised word that does not
  start a sentence), then a contrast word (`but`, `never`, `instead`, ...).
- `numbers`: numbers and amounts only.
- `words`: the caller's list: a word (marked in every cue that has it) or
  `{cue_id, word, index?}`. The response's `marks` have that shape, so a person or an agent
  can edit the automatic choice and send it back.

At most one mark per cue, two on a cue of `LONG_CUE_WORDS` words or more; a filler word is
never marked (an explicit one is listed in `skipped`). Each mark carries a one-line reason.
Words are the cue's space-separated tokens as drawn (a speaker prefix counts as tokens but
is never marked); a cue with ruby is not marked. The rules read Latin-script text: a
language written without spaces gets numbers only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .errors import SubtitleSkillError
from .models import SubtitleCue, SubtitleDocument

MODES = ("off", "auto", "numbers", "words")
EMPHASIS_FIELDS = frozenset({"mode", "words", "color"})
DEFAULT_COLOR = "FFD200"
LONG_CUE_WORDS = 8
MAX_MARKS = 1
MAX_MARKS_LONG = 2

FILLERS = frozenset({
    "um", "umm", "uh", "uhh", "er", "erm", "ah", "eh", "hmm", "mm", "like", "so", "well", "just", "really",
    "basically", "actually", "literally", "totally", "very", "you", "know", "i", "mean", "kind", "sort", "of",
    "okay", "ok", "right", "yeah", "yes", "oh", "the", "a", "an", "and", "or", "to", "is", "it", "that",
})
CONTRAST = frozenset({
    "but", "however", "instead", "never", "not", "although", "though", "yet", "except", "unless", "only",
    "rather", "despite", "nobody", "nothing", "none", "without",
})
_NOT_NAMES = frozenset({"i", "i'm", "i've", "i'll", "i'd", "ok", "okay"})
_SPACE = re.compile(r"([ \t\n]+)")
_EDGE = re.compile(r"^([\"'(\[“‘]*)(.*?)([\"')\].,!?;:”’…]*)$", re.S)
_SENTENCE_END = (".", "!", "?", "…", ":")


@dataclass(frozen=True)
class EmphasisSpec:
    mode: str
    words: tuple  # ((cue_id or None, word, index or None), ...)
    color: str
    raw: Any


def parse_emphasis(raw: Any) -> Optional[EmphasisSpec]:
    """None when nothing is to be marked (absent or `off`)."""
    if raw is None or raw == "off":
        return None
    if isinstance(raw, str):
        if raw not in MODES or raw == "words":
            raise SubtitleSkillError("INVALID_INPUT", f"emphasis must be one of {list(MODES)} (words as an object), "
                                     f"got {raw!r}", details={"field": "emphasis"})
        return EmphasisSpec(raw, (), DEFAULT_COLOR, raw)
    if not isinstance(raw, Mapping):
        raise SubtitleSkillError("INVALID_INPUT", "emphasis must be a mode name or an object",
                                 details={"field": "emphasis"})
    unknown = set(raw) - EMPHASIS_FIELDS
    if unknown:
        raise SubtitleSkillError("INVALID_INPUT", f"emphasis: unknown field(s) {sorted(unknown)}",
                                 details={"field": "emphasis"})
    mode = raw.get("mode", "words" if "words" in raw else None)
    if mode not in MODES:
        raise SubtitleSkillError("INVALID_INPUT", f"emphasis.mode must be one of {list(MODES)}, got {mode!r}",
                                 details={"field": "emphasis.mode"})
    color = raw.get("color", DEFAULT_COLOR)
    if not isinstance(color, str) or not re.match(r"^(?:#|0x)?[0-9A-Fa-f]{6}$", color.strip()):
        raise SubtitleSkillError("INVALID_INPUT", f"emphasis.color must be RRGGBB hex, got {color!r}",
                                 details={"field": "emphasis.color"})
    color = color.strip().lstrip("#")
    color = color[2:] if color.lower().startswith("0x") else color
    words: list = []
    if mode == "words":
        entries = raw.get("words")
        if not isinstance(entries, list) or not entries:
            raise SubtitleSkillError("INVALID_INPUT", "emphasis mode 'words' needs a non-empty `words` list",
                                     details={"field": "emphasis.words"})
        for i, entry in enumerate(entries):
            if isinstance(entry, str) and entry.strip():
                words.append((None, entry.strip(), None))
            elif isinstance(entry, Mapping) and set(entry) <= {"cue_id", "word", "index", "reason"} \
                    and isinstance(entry.get("word"), str) and entry["word"].strip() \
                    and (entry.get("cue_id") is None or isinstance(entry.get("cue_id"), str)) \
                    and (entry.get("index") is None or (isinstance(entry["index"], int)
                                                         and not isinstance(entry["index"], bool)
                                                         and entry["index"] >= 0)):
                words.append((entry.get("cue_id"), entry["word"].strip(), entry.get("index")))
            else:
                raise SubtitleSkillError(
                    "INVALID_INPUT", f"emphasis.words[{i}] must be a word or {{cue_id, word, index?}}",
                    details={"field": f"emphasis.words[{i}]"})
    elif raw.get("words") is not None:
        raise SubtitleSkillError("INVALID_INPUT", "emphasis.words is for mode 'words'",
                                 details={"field": "emphasis.words"})
    if mode == "off":
        return None
    return EmphasisSpec(mode, tuple(words), color.upper(), dict(raw))


def drawn_tokens(cue: SubtitleCue) -> Tuple[List[str], int]:
    """The cue's drawn text split at spaces (a no-break space does not split), and how many
    of the leading tokens are the speaker prefix."""
    prefix = f"{cue.speaker}: " if cue.speaker else ""
    tokens = [t for t in _SPACE.split(prefix + cue.text) if t and not _SPACE.fullmatch(t)]
    skip = len([t for t in _SPACE.split(prefix) if t and not _SPACE.fullmatch(t)])
    return tokens, skip


def split_edges(token: str) -> Tuple[str, str, str]:
    m = _EDGE.match(token)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", token, "")


def _is_number(core: str) -> bool:
    return any(ch.isdigit() for ch in core) or any(ch in core for ch in "$€£¥%")


def _classify(tokens: Sequence[str], k: int, skip: int) -> Optional[Tuple[int, str]]:
    _lead, core, _trail = split_edges(tokens[k])
    low = core.lower()
    if not core or low in FILLERS:
        return None
    if _is_number(core):
        return 0, "number or amount"
    sentence_start = k == skip or (k > skip and split_edges(tokens[k - 1])[2].endswith(_SENTENCE_END))
    if core[0].isupper() and len(core) >= 2 and not sentence_start and low not in _NOT_NAMES \
            and any(ch.isalpha() for ch in core):
        return 1, "proper name (capitalised mid-sentence)"
    if low in CONTRAST:
        return 2, "contrast word"
    return None


def plan_emphasis(document: SubtitleDocument, spec: EmphasisSpec) -> dict:
    """{marks: [{cue_id, word, index, reason}], skipped: [...]}; `index` counts the drawn tokens."""
    marks: list = []
    skipped: list = []
    for cue in document.cues:
        tokens, skip = drawn_tokens(cue)
        limit = MAX_MARKS_LONG if len(tokens) - skip >= LONG_CUE_WORDS else MAX_MARKS
        if cue.ruby:
            if spec.mode == "words" and any(cid in (None, cue.id) for cid, _w, _i in spec.words):
                skipped.append({"cue_id": cue.id, "word": None, "reason": "a cue with ruby is not marked"})
            continue
        chosen: list = []
        if spec.mode in ("auto", "numbers"):
            ranked = []
            for k in range(skip, len(tokens)):
                hit = _classify(tokens, k, skip)
                if hit is None or (spec.mode == "numbers" and hit[0] != 0):
                    continue
                ranked.append((hit[0], k, hit[1]))
            ranked.sort()
            chosen = [(k, reason) for _rank, k, reason in ranked[:limit]]
        else:
            for cue_id, word, index in spec.words:
                if cue_id is not None and cue_id != cue.id:
                    continue
                want = split_edges(word)[1].lower()
                found = None
                for k in range(skip, len(tokens)):
                    if (index is None or index == k) and split_edges(tokens[k])[1].lower() == want:
                        found = k
                        break
                if found is None:
                    if cue_id is not None:
                        skipped.append({"cue_id": cue.id, "word": word, "reason": "not in the cue's drawn text"})
                    continue
                if want in FILLERS:
                    skipped.append({"cue_id": cue.id, "word": word, "reason": "filler word, never highlighted"})
                    continue
                if any(k == found for k, _r in chosen):
                    continue
                if len(chosen) >= limit:
                    skipped.append({"cue_id": cue.id, "word": word,
                                    "reason": f"at most {limit} highlight(s) on this cue"})
                    continue
                chosen.append((found, "requested"))
        for k, reason in sorted(chosen):
            marks.append({"cue_id": cue.id, "word": split_edges(tokens[k])[1], "index": k, "reason": reason})
    return {"mode": spec.mode, "color": spec.color, "marks": marks, "skipped": skipped}


def marks_by_cue(plan: Mapping[str, Any]) -> dict:
    out: dict = {}
    for mark in plan.get("marks", ()):
        out.setdefault(mark["cue_id"], set()).add(mark["index"])
    return out
