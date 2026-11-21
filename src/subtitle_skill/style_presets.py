"""Style presets as data (docs/adr/ADR-015-style-presets.md, ADR-013 part 5).

`render` `style_preset` (opt-in, burn only) names a caption look: a bundled preset
(`presets/<name>.json` in this package) or `{"path": "<workspace JSON>"}`, the caller's
own file. A preset is a JSON object read against an allowlist: every key is one of
`FIELDS`, every value is checked here, and anything else is `INVALID_INPUT` with
`details.field` naming the key (`style_preset.<key>`). A preset is data only: it never
carries a command, a file path or ASS override tags (no `{`, `\\` or `/` in any string).

Sizes are percent of the frame height, so one preset looks the same on any resolution.
Precedence: a request's explicit fields (cue `style.color` / `bold` / `size`,
`emphasis.color`, `fonts_dir`, a `placement` move) override the preset, and the preset
overrides this skill's defaults.

Fonts: none bundled. `font` is a family or a fallback chain of families commonly installed
(DejaVu, Liberation, Noto); the first one fontconfig lists is used (`resolve_font`), and
libass falls back through fontconfig for a glyph the family lacks.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from .errors import SubtitleSkillError

PRESET_DIR = Path(__file__).parent / "presets"
BUNDLED = ("clean-lower-third", "bold-short-form", "minimal-serif", "boxed")

FIELDS = frozenset({
    "name", "font", "size", "color", "outline_color", "outline", "shadow", "case", "position", "margin",
    "bold", "box", "highlight", "animation",
})
HIGHLIGHT_FIELDS = frozenset({"color", "bold"})
CASES = ("none", "upper", "lower")
POSITIONS = ("bottom", "top", "lower-third")
BOXES = ("none", "scrim")
#: `animate` / `karaoke` are drawn by ffmpeg-skill caption from an SRT; a preset burns through
#: this skill's own ASS, which caption.py burns as it is (ADR-011 point 4), so only "none" exists
ANIMATIONS = ("none",)
#: inclusive ranges, percent of the frame height
RANGES = {"size": (2.0, 20.0), "outline": (0.0, 2.0), "shadow": (0.0, 2.0), "margin": (0.0, 30.0)}
MAX_FONTS = 6
MAX_FILE_BYTES = 16 * 1024

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")
#: a family name: letters, digits, spaces and `._+-'&`; never a path separator, a comma (an ASS
#: style is comma separated), a brace or a backslash (override tags), or a control character
_FAMILY = re.compile(r"^[\w .+'&-]{1,64}$", re.UNICODE)

DEFAULTS = {
    "case": "none", "position": "bottom", "bold": False, "box": "none", "animation": "none",
    "outline_color": "000000",
}


@dataclass(frozen=True)
class Preset:
    """A checked preset with its defaults filled in. `size`, `outline`, `shadow` and `margin`
    are percent of the frame height; colours are `RRGGBB`."""

    name: str
    fonts: Tuple[str, ...]
    size: Optional[float]
    color: Optional[str]
    outline_color: str
    outline: Optional[float]
    shadow: Optional[float]
    case: str
    position: str
    margin: Optional[float]
    bold: bool
    box: str
    highlight_color: Optional[str]
    highlight_bold: bool
    animation: str
    source: str  # "bundled" or the workspace path as the request gave it

    def to_dict(self) -> dict:
        return {
            "name": self.name, "source": self.source, "font": list(self.fonts), "size": self.size,
            "color": self.color, "outline_color": self.outline_color, "outline": self.outline,
            "shadow": self.shadow, "case": self.case, "position": self.position, "margin": self.margin,
            "bold": self.bold, "box": self.box,
            "highlight": {"color": self.highlight_color, "bold": self.highlight_bold},
            "animation": self.animation,
        }

    def apply_case(self, text: str) -> str:
        if self.case == "upper":
            return text.upper()
        if self.case == "lower":
            return text.lower()
        return text


def _fail(field: str, message: str) -> SubtitleSkillError:
    return SubtitleSkillError("INVALID_INPUT", f"style_preset: {message}", details={"field": field})


def _number(raw: Mapping[str, Any], key: str) -> Optional[float]:
    if key not in raw:
        return None
    value = raw[key]
    low, high = RANGES[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (low <= value <= high):
        raise _fail(f"style_preset.{key}", f"{key} must be a number from {low:g} to {high:g} "
                                           f"(percent of the frame height), got {value!r}")
    return float(value)


def _color(value: Any, field: str) -> str:
    m = _HEX.match(value) if isinstance(value, str) else None
    if not m:
        raise _fail(field, f"{field.split('.', 1)[1]} must be RRGGBB hex, got {value!r}")
    return m.group(1).upper()


def _choice(raw: Mapping[str, Any], key: str, choices: tuple) -> str:
    value = raw.get(key, DEFAULTS[key])
    if value not in choices:
        raise _fail(f"style_preset.{key}", f"{key} must be one of {list(choices)}, got {value!r}")
    return value


def _fonts(value: Any) -> Tuple[str, ...]:
    families = [value] if isinstance(value, str) else value
    if not isinstance(families, list) or not 1 <= len(families) <= MAX_FONTS:
        raise _fail("style_preset.font", f"font must be a family name or a list of 1 to {MAX_FONTS} family names")
    out = []
    for family in families:
        if not isinstance(family, str) or not _FAMILY.match(family) or not family.strip():
            raise _fail("style_preset.font", f"font must name a family (letters, digits, spaces), never a path or "
                                             f"tags: {family!r}")
        out.append(family.strip())
    return tuple(out)


def validate(raw: Any, *, source: str) -> Preset:
    """`raw` checked against the allowlist; the first bad key raises with its name."""
    if not isinstance(raw, Mapping):
        raise _fail("style_preset", "a preset must be a JSON object")
    for key in raw:
        if key not in FIELDS:
            raise _fail(f"style_preset.{key}", f"unknown field {key!r}; a preset has only {sorted(FIELDS)}")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise _fail("style_preset.name", f"name must be lower-case letters, digits and hyphens, got {name!r}")
    fonts = _fonts(raw["font"]) if "font" in raw else ()
    highlight = raw.get("highlight", {})
    if not isinstance(highlight, Mapping):
        raise _fail("style_preset.highlight", "highlight must be an object {color, bold}")
    for key in highlight:
        if key not in HIGHLIGHT_FIELDS:
            raise _fail(f"style_preset.highlight.{key}", f"unknown field {key!r}; highlight has only color and bold")
    highlight_bold = highlight.get("bold", True)
    if not isinstance(highlight_bold, bool):
        raise _fail("style_preset.highlight.bold", "highlight.bold must be true or false")
    bold = raw.get("bold", DEFAULTS["bold"])
    if not isinstance(bold, bool):
        raise _fail("style_preset.bold", "bold must be true or false")
    animation = raw.get("animation", DEFAULTS["animation"])
    if animation not in ANIMATIONS:
        raise _fail("style_preset.animation",
                    f"animation must be 'none', got {animation!r}: a preset burns through this skill's own ASS, which "
                    "ffmpeg-skill caption does not animate (docs/adr/ADR-011-animated-captions.md)")
    return Preset(
        name=name,
        fonts=fonts,
        size=_number(raw, "size"),
        color=_color(raw["color"], "style_preset.color") if "color" in raw else None,
        outline_color=_color(raw.get("outline_color", DEFAULTS["outline_color"]), "style_preset.outline_color"),
        outline=_number(raw, "outline"),
        shadow=_number(raw, "shadow"),
        case=_choice(raw, "case", CASES),
        position=_choice(raw, "position", POSITIONS),
        margin=_number(raw, "margin"),
        bold=bold,
        box=_choice(raw, "box", BOXES),
        highlight_color=_color(highlight["color"], "style_preset.highlight.color") if "color" in highlight else None,
        highlight_bold=highlight_bold,
        animation=animation,
        source=source,
    )


def load_bundled(name: str) -> Preset:
    if name not in BUNDLED:
        raise _fail("style_preset", f"unknown preset {name!r}; bundled: {list(BUNDLED)} (or {{\"path\": ...}})")
    raw = json.loads((PRESET_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return validate(raw, source="bundled")


def parse_style_preset(raw: Any, policy) -> Optional[Preset]:
    """`request.style_preset`: None when absent, else the checked preset."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return load_bundled(raw)
    if not isinstance(raw, Mapping) or set(raw) != {"path"} or not isinstance(raw["path"], str) or not raw["path"]:
        raise _fail("style_preset", "style_preset must be a bundled preset name or {\"path\": \"<workspace JSON>\"}")
    path = policy.resolve_input(raw["path"])
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise _fail("style_preset.path", f"a preset file must be a JSON file of at most {MAX_FILE_BYTES} bytes")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("style_preset.path", f"the preset file is not JSON: {exc}") from None
    # the allowlist is stricter than the request's forbidden-key screen: any key not in FIELDS
    # (a `command`, a `path`, an ASS `override`) is refused with its name
    return validate(data, source=raw["path"])


def installed_families() -> Optional[frozenset]:
    """The families fontconfig lists (lower case), or None without `fc-list`."""
    exe = shutil.which("fc-list")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, ":", "family"], capture_output=True, text=True, timeout=30, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return frozenset(name.strip().lower() for line in out.splitlines() for name in line.split(",") if name.strip())


def resolve_font(fonts: Tuple[str, ...], installed: Optional[frozenset]) -> Tuple[Optional[str], bool]:
    """(family, found): the first family of the chain fontconfig lists, or the first of the
    chain when none is listed or fontconfig cannot be asked (libass then substitutes)."""
    if not fonts:
        return None, False
    if installed is not None:
        for family in fonts:
            if family.lower() in installed:
                return family, True
    return fonts[0], False
