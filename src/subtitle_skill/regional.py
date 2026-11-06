"""Regional caption profiles: broadcast conventions as opt-in layers over the
international defaults (roadmap step 10, docs/adr/ADR-008-regional-profiles.md).

Without a profile nothing here runs: white text, a `Speaker: ` prefix, the locale
profile's line limits (ADR-001). A profile is chosen by name in the request
(`regional_profile`); it never switches on by itself from the document language.

`ja-broadcast` (Japanese broadcast captions):

- speakers are told apart by colour, in order of first appearance: white,
  yellow, cyan, green (the four colours Japanese broadcast captions use for
  speakers); the name prefix is not drawn, the colour identifies the speaker;
- at most 15 full-width characters a line and 2 lines;
- ruby is drawn over its base (burn-in) and written as IMSC ruby (TTML).

Ruby and vertical text are document content, not profile features: a cue that
carries `ruby` is annotated with or without a profile.

Other regions are added as further entries of `PROFILES`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from .errors import SubtitleSkillError
from .models import SubtitleDocument, SubtitleStyle


@dataclass(frozen=True)
class RegionalProfile:
    name: str
    languages: tuple  # primary subtags this profile is for
    speaker_colors: tuple  # RRGGBB, in order of first appearance
    show_speaker_names: bool
    max_chars_per_line: Optional[float]
    max_lines: Optional[int]
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "languages": list(self.languages),
            "speaker_colors": list(self.speaker_colors),
            "show_speaker_names": self.show_speaker_names,
            "max_chars_per_line": self.max_chars_per_line,
            "max_lines": self.max_lines,
            "description": self.description,
        }


PROFILES = {
    "ja-broadcast": RegionalProfile(
        name="ja-broadcast",
        languages=("ja",),
        speaker_colors=("FFFFFF", "FFFF00", "00FFFF", "00FF00"),
        show_speaker_names=False,
        max_chars_per_line=15,
        max_lines=2,
        description=(
            "Japanese broadcast captions: speakers by colour (white, yellow, cyan, green in order of "
            "first appearance, no name prefix), 15 full-width characters x 2 lines, ruby over its base"
        ),
    ),
}


def profile_named(name) -> RegionalProfile:
    if not isinstance(name, str) or name not in PROFILES:
        raise SubtitleSkillError(
            "INVALID_INPUT", f"regional_profile must be one of {sorted(PROFILES)}, got {name!r}"
        )
    return PROFILES[name]


def check_language(profile: RegionalProfile, language: str) -> None:
    primary = language.split("-", 1)[0].lower()
    if primary not in profile.languages:
        raise SubtitleSkillError(
            "INVALID_INPUT",
            f"regional_profile {profile.name!r} is for {list(profile.languages)} documents, not {language!r}",
        )


def speaker_colors(profile: RegionalProfile, document: SubtitleDocument) -> dict:
    """speaker -> colour, in order of first appearance; past the palette the colours repeat."""
    out: dict = {}
    for cue in sorted(document.cues, key=lambda c: (c.start, c.end, c.id)):
        if cue.speaker and cue.speaker not in out:
            out[cue.speaker] = profile.speaker_colors[len(out) % len(profile.speaker_colors)]
    return out


def apply_profile(profile: RegionalProfile, document: SubtitleDocument, *, colors_carried: bool = True) -> tuple:
    """The document as the profile draws it, and the speaker colour map.

    Each speaker's cues get the speaker's colour (a cue's own `style.color` wins);
    with `show_speaker_names` False the speaker moves to the cue's metadata
    (`metadata.speaker`), so no generator draws the prefix. Where the output cannot
    carry colour (`colors_carried` False: SRT, WebVTT, a soft track) nothing is
    coloured and the names stay, since the colour is what would replace them."""
    check_language(profile, document.language)
    if not colors_carried:
        return document, {}
    colors = speaker_colors(profile, document)
    cues = []
    for cue in document.cues:
        if not cue.speaker:
            cues.append(cue)
            continue
        style = cue.style or SubtitleStyle()
        if style.color is None:
            style = replace(style, color=colors[cue.speaker])
        if profile.show_speaker_names:
            cues.append(replace(cue, style=style))
        else:
            cues.append(replace(cue, style=style, speaker=None, metadata={**cue.metadata, "speaker": cue.speaker}))
    return replace(document, cues=tuple(cues)), colors
