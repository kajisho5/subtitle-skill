"""Automatic legibility (docs/adr/ADR-013-caption-quality.md, part 4).

`render` `legibility: "auto"` (opt-in, burn only) measures, on frames sampled while each cue
is on screen, the picture behind the cue's text box, and adds a treatment only where the
text would not read:

- the background's relative luminance (WCAG 2 definition, sRGB linearised) at its 90th
  percentile: a bright patch is what washes out light text;
- the WCAG contrast ratio of the text colour against it;
- the spread between its 10th and 90th percentile (a busy background).

Rule, per cue, from the worst sampled frame: contrast under `SCRIM_BELOW` gets a scrim (a
semi-opaque dark box behind the text); under `HALO_BELOW`, or a spread over `BUSY_SPREAD`,
gets a halo (a wider, blurred dark outline); anything else is left exactly as it was. Text
darker than mid-grey is measured and reported but never treated (a dark scrim or halo
would not help it). Every cue's measurement and treatment is reported.

The frames are read with ffmpeg (decode only: one frame per sample, cropped to the box and
scaled down); nothing is written.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

from .errors import SubtitleSkillError

MODES = ("off", "auto")
SCRIM_BELOW = 3.0
HALO_BELOW = 4.5
BUSY_SPREAD = 0.45
SAMPLES = (0.2, 0.5, 0.8)  # fractions of the cue's duration
SAMPLE_WIDTH = 96  # px: the crop is scaled down to this width before it is read
#: the scrim's opacity: ASS alpha 0x60 (0 = opaque) over the frame
SCRIM_ALPHA = 0x60
HALO_OUTLINE_SCALE = 2.5
HALO_BLUR_SCALE = 1.5


def parse_legibility(raw) -> Optional[str]:
    if raw is None or raw == "off":
        return None
    if raw != "auto":
        raise SubtitleSkillError("INVALID_INPUT", f"legibility must be one of {list(MODES)}, got {raw!r}",
                                 details={"field": "legibility"})
    return "auto"


def _linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(r: int, g: int, b: int) -> float:
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def hex_luminance(color: str) -> float:
    color = color.strip().lstrip("#")
    color = color[2:] if color.lower().startswith("0x") else color
    return relative_luminance(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def contrast_ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


@dataclass(frozen=True)
class Region:
    """A cue's text box in output pixels, and when it is on screen."""

    cue_id: str
    x: int
    y: int
    w: int
    h: int
    start: float
    end: float
    text_color: str


def _frame_luminances(ffmpeg: str, video: Path, t: float, region: Region, width: int, height: int) -> List[float]:
    x = max(0, min(region.x, width - 2))
    y = max(0, min(region.y, height - 2))
    w = max(2, min(region.w, width - x))
    h = max(2, min(region.h, height - y))
    out_w = max(2, min(SAMPLE_WIDTH, w))
    out_h = max(2, round(h * out_w / w))
    argv = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(video), "-map", "0:V:0", "-frames:v", "1",
            "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{out_h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-"]
    proc = subprocess.run(argv, capture_output=True, timeout=120, shell=False)
    if proc.returncode != 0:
        raise SubtitleSkillError("DEPENDENCY_ERROR",
                                 f"ffmpeg could not read a frame at {t:.3f}s: {proc.stderr.decode(errors='replace')[-300:]}")
    data = proc.stdout
    return [relative_luminance(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data) - 2, 3)]


def measure(video: Path, regions: Sequence[Region], *, width: int, height: int) -> dict:
    """{cue_id: {treatment, contrast, background_luminance, spread, samples}}."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SubtitleSkillError("DEPENDENCY_ERROR", "legibility 'auto' needs ffmpeg on PATH")
    out: dict = {}
    for region in regions:
        worst = None
        times = []
        for frac in SAMPLES:
            t = region.start + frac * (region.end - region.start)
            times.append(round(t, 3))
            lum = _frame_luminances(ffmpeg, video, t, region, width, height)
            p90, p10 = _percentile(lum, 0.9), _percentile(lum, 0.1)
            if worst is None or p90 > worst[0]:
                worst = (p90, p90 - p10)
            elif p90 - p10 > worst[1]:
                worst = (worst[0], p90 - p10)
        background, spread = worst
        text_lum = hex_luminance(region.text_color)
        contrast = contrast_ratio(text_lum, background)
        if text_lum < 0.18:
            treatment, why = "none", "dark text: measured, not treated"
        elif contrast < SCRIM_BELOW:
            treatment, why = "scrim", f"contrast {contrast:.2f} under {SCRIM_BELOW}"
        elif contrast < HALO_BELOW:
            treatment, why = "halo", f"contrast {contrast:.2f} under {HALO_BELOW}"
        elif spread > BUSY_SPREAD:
            treatment, why = "halo", f"busy background (spread {spread:.2f} over {BUSY_SPREAD})"
        else:
            treatment, why = "none", "reads as it is"
        out[region.cue_id] = {
            "treatment": treatment,
            "reason": why,
            "contrast": round(contrast, 2),
            "background_luminance": round(background, 4),
            "spread": round(spread, 4),
            "box": [region.x, region.y, region.w, region.h],
            "sampled_at": times,
        }
    return out


def scrim_contrast(text_color: str, background: float) -> float:
    """Contrast of the text against the scrim over `background`: libass blends in the
    encoded (gamma) values, so the box's grey level is mixed before it is linearised."""
    opacity = 1.0 - SCRIM_ALPHA / 255.0
    # the background's encoded level from its linear luminance
    encoded = background * 12.92 if background <= 0.0031308 else 1.055 * background ** (1 / 2.4) - 0.055
    mixed = encoded * (1.0 - opacity) * 255.0
    lum = _linear(mixed)
    return round(contrast_ratio(hex_luminance(text_color), lum), 2)
