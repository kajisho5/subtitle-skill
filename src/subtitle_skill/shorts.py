"""The `shorts` caption profile: burned captions for vertical clips (roadmap step 11,
docs/adr/ADR-009-shorts-captions.md).

- **One or two words per cue**, from the cues' word timings (`cue.words`, as a
  transcription engine reports them). A chunk ends after two words, after a word
  that ends a clause (`.,!?;:` and their full-width forms, `、。`), and at the end
  of the cue. It is on screen from its first word's start to its last word's end,
  held to the next chunk's start when the gap is under `HOLD_GAP` so captions do
  not flicker between words.
- **Large type inside the safe zone** every 9:16 destination leaves clear: the
  intersection of ffmpeg-skill's TikTok, YouTube Shorts and Instagram Reels safe
  zones (`_platforms.py`: top 10 %, bottom 22 %, left 5 %, right 14 % of the frame).
  Text is 5.5 % of the frame height, bold, centred in the safe zone's width, its
  bottom edge at 70 % of the height; a chunk estimated wider than the safe zone
  is drawn smaller to fit.
- **The speaker's face avoided** with step 8's rule (placement.py): while a face
  overlaps the caption box the chunk moves to the top of the safe zone.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace

from .errors import SubtitleSkillError
from .models import SubtitleCue, SubtitleDocument
from .placement import Box

#: ffmpeg-skill `scripts/_platforms.py` (2.24.0, unchanged on main): the fraction of
#: the frame each edge's app UI covers on these 9:16 destinations.
PLATFORM_SAFE = {
    "tiktok": {"top": 0.10, "bottom": 0.22, "left": 0.05, "right": 0.14},
    "shorts": {"top": 0.06, "bottom": 0.18, "left": 0.05, "right": 0.12},
    "reels": {"top": 0.08, "bottom": 0.20, "left": 0.05, "right": 0.12},
}
SAFE = {edge: max(zone[edge] for zone in PLATFORM_SAFE.values()) for edge in ("top", "bottom", "left", "right")}

FONT_HEIGHT = 0.055  # Fontsize as a fraction of the frame height
CAPTION_BOTTOM = 0.70  # bottom edge of the caption box, fraction of the height
OUTLINE = 0.004  # outline width, fraction of the height
MAX_WORDS = 2
HOLD_GAP = 0.3  # seconds

#: estimated advance per character as a multiple of the Fontsize (libass sizes a font
#: by its ascent + descent). Measured on libass burns at Fontsize 100: DejaVu Sans Bold
#: "Hello world" 48.5 px a character, "everything" 51.3, "mmmmmmmmmm" 87.9; Noto Sans
#: CJK full-width 69 (ADR-008). Latin is taken as 0.6 and a chunk is fitted to 90 % of
#: the safe width; the burn test checks the drawn pixels, not this estimate.
LATIN_ADVANCE = 0.6
CJK_ADVANCE = 0.69
FIT = 0.9

_CLAUSE_END = tuple(".,!?;:。、！？；：…")
_NO_SPACE_LANGUAGES = ("ja", "zh")


@dataclass(frozen=True)
class ShortsLayout:
    width: int
    height: int

    @property
    def safe_box(self) -> Box:
        return Box(SAFE["left"], SAFE["top"], 1.0 - SAFE["left"] - SAFE["right"], 1.0 - SAFE["top"] - SAFE["bottom"])

    @property
    def font_size(self) -> int:
        return max(1, round(FONT_HEIGHT * self.height))

    @property
    def box_height(self) -> float:
        return FONT_HEIGHT * 1.25

    def caption_boxes(self) -> dict:
        safe = self.safe_box
        return {
            "bottom": Box(safe.x, CAPTION_BOTTOM - self.box_height, safe.w, self.box_height),
            "top": Box(safe.x, safe.y, safe.w, self.box_height),
        }

    def centre_x(self) -> float:
        return (SAFE["left"] + (1.0 - SAFE["right"])) / 2.0 * self.width

    def fit_font_size(self, text: str) -> int:
        """The Fontsize for `text`: the profile's, or smaller so it fits the safe width."""
        size = self.font_size
        width = estimate_width(text, size)
        room = self.safe_box.w * self.width * FIT
        if width > room:
            size = max(1, int(size * room / width))
        return size


def estimate_width(text: str, font_size: float) -> float:
    return sum(
        font_size * (CJK_ADVANCE if unicodedata.east_asian_width(ch) in ("W", "F") else LATIN_ADVANCE)
        for ch in text
    )


def _joiner(language: str) -> str:
    return "" if language.split("-", 1)[0].lower() in _NO_SPACE_LANGUAGES else " "


def chunk_document(document: SubtitleDocument) -> SubtitleDocument:
    """The document as `shorts` cues of at most two words each."""
    missing = [c.id for c in document.cues if not c.words]
    if missing:
        raise SubtitleSkillError(
            "MISSING_INPUT",
            f"caption_profile 'shorts' needs word timings (cue.words) on every cue; missing on {missing[:5]}",
        )
    joiner = _joiner(document.language)
    chunks = []
    for cue in sorted(document.cues, key=lambda c: (c.start, c.end, c.id)):
        group: list = []
        groups = []
        for word in cue.words:
            group.append(word)
            if len(group) == MAX_WORDS or word.text.endswith(_CLAUSE_END):
                groups.append(group)
                group = []
        if group:
            groups.append(group)
        for i, words in enumerate(groups):
            chunks.append((cue, i, words))

    cues = []
    for n, (cue, i, words) in enumerate(chunks):
        start, end = words[0].start, words[-1].end
        if n + 1 < len(chunks):
            next_start = chunks[n + 1][2][0].start
            if 0 <= next_start - end < HOLD_GAP:
                end = next_start
        cues.append(
            replace(
                cue,
                id=f"{cue.id}.{i + 1}",
                start=start,
                end=end,
                text=joiner.join(w.text for w in words),
                ruby=(),
                words=tuple(words),
                # a name prefix would be a third word: the speaker is kept in metadata
                speaker=None,
                metadata={**cue.metadata, "source_cue": cue.id,
                          **({"speaker": cue.speaker} if cue.speaker else {})},
            )
        )
    return replace(document, cues=tuple(cues))


def word_count(cue: SubtitleCue) -> int:
    return len(cue.words)
