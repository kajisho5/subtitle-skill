"""Caption placement away from faces and caller-named regions (roadmap step 8,
docs/adr/ADR-006-caption-placement.md).

Each cue sits in the bottom caption box unless, while it is on screen, a face
from a detection-skill `detections@1` document (or a region the caller names)
overlaps that box; then the whole cue moves to the top caption box. Decisions
are per cue, never mid-cue. A short run of bottom cues between two moved cues
is moved as well when it would be on screen for less than `min_hold` seconds,
so captions do not jump back and forth between neighbouring cues. Every move
records the box that caused it.

This is a mechanical rule over boxes measured by another skill. Nothing here
decides what a face is or whose it is: a face box is a rectangle.

Coordinates are fractions of the output frame (x, w by width; y, h by height),
top-left origin. The caption boxes follow the burn layout `formats.ass` writes,
which reproduces ffmpeg-skill caption.py's own defaults (24 pt type and a 30
unit vertical margin on a 288-line script, 5 % side margins).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .errors import SubtitleSkillError
from .models import SubtitleDocument
from .provenance import sha256_file

DETECTIONS_SCHEMA = "detection-skill/detections@1"
REFRAME_SCHEMA = "detection-skill/reframe@1"

#: caption.py's defaults on its 288-line script (its `--size` 24, `--margin` 30).
SCRIPT_HEIGHT = 288.0
DEFAULT_FONT_POINTS = 24.0
DEFAULT_MARGIN_POINTS = 30.0
#: line advance as a multiple of the font size (libass with common fonts: about 1.2;
#: 1.25 keeps the box a little larger than the drawn text).
LINE_ADVANCE = 1.25
#: side margin of the caption box, a fraction of the width (EBU R 95 graphics-safe 5 %).
SIDE_MARGIN = 0.05
DEFAULT_MIN_HOLD = 1.0
DEFAULT_MIN_SCORE = 0.0

PLACEMENT_FIELDS = frozenset({"faces", "reframe", "avoid", "min_hold", "max_lines", "min_score"})
_REGION_KEYS = frozenset({"name", "box", "start", "end"})


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    def intersects(self, other: "Box") -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )

    def as_list(self) -> list:
        return [round(self.x, 5), round(self.y, 5), round(self.w, 5), round(self.h, 5)]


def caption_boxes(max_lines: int = 2, *, font_points: float = DEFAULT_FONT_POINTS,
                  margin_points: float = DEFAULT_MARGIN_POINTS) -> dict:
    """The bottom and top caption boxes, as fractions of the frame."""
    height = max_lines * LINE_ADVANCE * font_points / SCRIPT_HEIGHT
    margin = margin_points / SCRIPT_HEIGHT
    width = 1.0 - 2 * SIDE_MARGIN
    return {
        "bottom": Box(SIDE_MARGIN, 1.0 - margin - height, width, height),
        "top": Box(SIDE_MARGIN, margin, width, height),
    }


@dataclass(frozen=True)
class Obstacle:
    """Something a caption must not cover over [start, end): a face sample or a region."""

    kind: str  # "face" | "region"
    box: Box
    start: float
    end: float
    t: Optional[float] = None  # face: the sample time
    name: Optional[str] = None  # region: the caller's name
    source_box: Optional[list] = None  # face: [x, y, w, h] in source pixels


@dataclass(frozen=True)
class PlacementSpec:
    faces_path: Optional[Path]
    reframe_path: Optional[Path]
    regions: tuple
    min_hold: float
    max_lines: int
    min_score: float
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PlacementPlan:
    positions: dict  # cue id -> "bottom" | "top"
    moves: list
    unavoidable: list
    inputs: dict

    def top_cue_ids(self) -> frozenset:
        return frozenset(cid for cid, pos in self.positions.items() if pos == "top")

    def to_dict(self) -> dict:
        return {
            "moves": list(self.moves),
            "unavoidable": list(self.unavoidable),
            "inputs": dict(self.inputs),
            "top_cues": sorted(self.top_cue_ids()),
        }


def _number(value: Any, what: str, *, low: Optional[float] = None, high: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SubtitleSkillError("INVALID_INPUT", f"{what} must be a number, got {value!r}")
    value = float(value)
    if (low is not None and value < low) or (high is not None and value > high):
        raise SubtitleSkillError("INVALID_INPUT", f"{what} must be in [{low}, {high}], got {value!r}")
    return value


def _norm_box(raw: Any, what: str) -> Box:
    if not isinstance(raw, list) or len(raw) != 4:
        raise SubtitleSkillError("INVALID_INPUT", f"{what} must be [x, y, w, h] as fractions of the frame")
    x, y, w, h = (_number(v, what, low=0.0, high=1.0) for v in raw)
    if w <= 0 or h <= 0 or x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
        raise SubtitleSkillError("INVALID_INPUT", f"{what} must lie inside the frame with positive size, got {raw!r}")
    return Box(x, y, w, h)


def parse_placement(raw: Any, policy) -> PlacementSpec:
    """Parse `request.placement` (render only). Paths resolve inside the workspace."""
    if not isinstance(raw, Mapping):
        raise SubtitleSkillError("INVALID_INPUT", "request.placement must be an object")
    unknown = set(raw) - PLACEMENT_FIELDS
    if unknown:
        raise SubtitleSkillError("INVALID_INPUT", f"unknown placement field(s): {sorted(unknown)}")

    faces_path = None
    if raw.get("faces") is not None:
        if not isinstance(raw["faces"], str) or not raw["faces"]:
            raise SubtitleSkillError("INVALID_INPUT", "placement.faces must be a workspace-relative path")
        faces_path = policy.resolve_input(raw["faces"])
    reframe_path = None
    if raw.get("reframe") is not None:
        if faces_path is None:
            raise SubtitleSkillError("INVALID_INPUT", "placement.reframe needs placement.faces (it maps their boxes)")
        if not isinstance(raw["reframe"], str) or not raw["reframe"]:
            raise SubtitleSkillError("INVALID_INPUT", "placement.reframe must be a workspace-relative path")
        reframe_path = policy.resolve_input(raw["reframe"])

    regions = []
    avoid = raw.get("avoid", [])
    if not isinstance(avoid, list):
        raise SubtitleSkillError("INVALID_INPUT", "placement.avoid must be an array of regions")
    for i, region in enumerate(avoid):
        if not isinstance(region, Mapping):
            raise SubtitleSkillError("INVALID_INPUT", f"placement.avoid[{i}] must be an object")
        unknown = set(region) - _REGION_KEYS
        if unknown:
            raise SubtitleSkillError("INVALID_INPUT", f"placement.avoid[{i}]: unknown field(s) {sorted(unknown)}")
        name = region.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SubtitleSkillError("INVALID_INPUT", f"placement.avoid[{i}].name must be a non-empty string")
        box = _norm_box(region.get("box"), f"placement.avoid[{i}].box")
        start = _number(region.get("start", 0.0), f"placement.avoid[{i}].start", low=0.0)
        end = region.get("end")
        end = float("inf") if end is None else _number(end, f"placement.avoid[{i}].end", low=0.0)
        if end <= start:
            raise SubtitleSkillError("INVALID_INPUT", f"placement.avoid[{i}]: end must be after start")
        regions.append(Obstacle("region", box, start, end, name=name))

    if faces_path is None and not regions:
        raise SubtitleSkillError("MISSING_INPUT", "request.placement needs placement.faces or placement.avoid")

    min_hold = _number(raw.get("min_hold", DEFAULT_MIN_HOLD), "placement.min_hold", low=0.0, high=60.0)
    max_lines = raw.get("max_lines", 2)
    if isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 1 <= max_lines <= 4:
        raise SubtitleSkillError("INVALID_INPUT", "placement.max_lines must be an integer from 1 to 4")
    min_score = _number(raw.get("min_score", DEFAULT_MIN_SCORE), "placement.min_score", low=0.0, high=1.0)
    return PlacementSpec(faces_path, reframe_path, tuple(regions), min_hold, max_lines, min_score, dict(raw))


def _load_json(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubtitleSkillError("INVALID_INPUT", f"{what} is not a readable JSON document: {exc}") from None
    if not isinstance(data, Mapping):
        raise SubtitleSkillError("INVALID_INPUT", f"{what} must be a JSON object")
    return data


def _frame_size(doc: Mapping[str, Any], what: str) -> tuple:
    size = doc.get("frame_size")
    try:
        width, height = float(size["width"]), float(size["height"])
    except (TypeError, KeyError, ValueError):
        raise SubtitleSkillError("INVALID_INPUT", f"{what}: frame_size {{width, height}} is required") from None
    if width <= 0 or height <= 0:
        raise SubtitleSkillError("INVALID_INPUT", f"{what}: frame_size must be positive")
    return width, height


def _reframe_windows(doc: Mapping[str, Any]) -> list:
    """(start, end, x, y, w, h) crop windows in source pixels: one per shot (what an
    engine without a time-varying crop applies), else one per sample time."""
    if doc.get("schema") != REFRAME_SCHEMA:
        raise SubtitleSkillError("INVALID_INPUT", f"placement.reframe must be a {REFRAME_SCHEMA} document")
    out = []
    shots = doc.get("shots") or []
    if shots:
        for s in shots:
            out.append((float(s["start"]), float(s["end"]), float(s["x"]), float(s["y"]),
                        float(s["width"]), float(s["height"])))
        return out
    windows = sorted(doc.get("windows") or [], key=lambda w: float(w["t"]))
    if not windows:
        raise SubtitleSkillError("INVALID_INPUT", "placement.reframe has neither shots nor windows")
    for i, w in enumerate(windows):
        start = float(w["t"]) if i else 0.0
        end = float(windows[i + 1]["t"]) if i + 1 < len(windows) else float("inf")
        out.append((start, end, float(w["x"]), float(w["y"]), float(w["width"]), float(w["height"])))
    return out


def _sampling_step(doc: Mapping[str, Any], times: Sequence[float]) -> float:
    sampling = doc.get("sampling") or {}
    every = sampling.get("every")
    if isinstance(every, (int, float)) and not isinstance(every, bool) and every > 0:
        return float(every)
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return min(gaps) if gaps else 0.0


def load_face_obstacles(spec: PlacementSpec) -> tuple:
    """Face samples as obstacles in output-frame fractions, plus input provenance."""
    if spec.faces_path is None:
        return [], {}, 0.0
    doc = _load_json(spec.faces_path, "placement.faces")
    if doc.get("schema") != DETECTIONS_SCHEMA or doc.get("kind") != "face":
        raise SubtitleSkillError(
            "INVALID_INPUT", f"placement.faces must be a {DETECTIONS_SCHEMA} document of kind 'face'"
        )
    src_w, src_h = _frame_size(doc, "placement.faces")
    frames = doc.get("frames")
    if not isinstance(frames, list):
        raise SubtitleSkillError("INVALID_INPUT", "placement.faces: frames must be an array (a video's detections)")
    windows = None
    inputs = {"faces": {"file": spec.faces_path.name, "sha256": sha256_file(spec.faces_path),
                        "frames": len(frames)}}
    if spec.reframe_path is not None:
        reframe = _load_json(spec.reframe_path, "placement.reframe")
        windows = _reframe_windows(reframe)
        inputs["reframe"] = {"file": spec.reframe_path.name, "sha256": sha256_file(spec.reframe_path),
                             "windows": len(windows)}

    times = []
    obstacles = []
    for frame in frames:
        t = frame.get("t") if isinstance(frame, Mapping) else None
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise SubtitleSkillError("INVALID_INPUT", "placement.faces: every frame needs a numeric t")
        t = float(t)
        times.append(t)
        for det in frame.get("detections") or []:
            score = det.get("score", 1.0)
            if isinstance(score, (int, float)) and score < spec.min_score:
                continue
            try:
                x, y, w, h = (float(v) for v in det["box"])
            except (TypeError, KeyError, ValueError):
                raise SubtitleSkillError("INVALID_INPUT", f"placement.faces: a detection at t={t} has no box") from None
            if windows is not None:
                win = next((wd for wd in windows if wd[0] <= t < wd[1]), None)
                if win is None:
                    continue
                _s, _e, wx, wy, ww, wh = win
                x0, y0 = max(x, wx), max(y, wy)
                x1, y1 = min(x + w, wx + ww), min(y + h, wy + wh)
                if x1 <= x0 or y1 <= y0:
                    continue  # the face is cropped away
                box = Box((x0 - wx) / ww, (y0 - wy) / wh, (x1 - x0) / ww, (y1 - y0) / wh)
            else:
                box = Box(x / src_w, y / src_h, w / src_w, h / src_h)
            obstacles.append(Obstacle("face", box, t, t, t=t, source_box=[round(v, 2) for v in (x, y, w, h)]))
    times.sort()
    return obstacles, inputs, _sampling_step(doc, times)


def _faces_during(obstacles: Sequence[Obstacle], start: float, end: float, step: float) -> list:
    """Face samples taken while the cue is on screen; for a cue shorter than the
    sampling step that holds no sample, the samples nearest its middle (within one step)."""
    inside = [o for o in obstacles if start <= o.t < end]
    if inside or step <= 0:
        return inside
    mid = (start + end) / 2.0
    near = [o for o in obstacles if abs(o.t - mid) <= step]
    if not near:
        return []
    best = min(abs(o.t - mid) for o in near)
    return [o for o in near if abs(o.t - mid) == best]


def _blocking(obstacles: Sequence[Obstacle], box: Box) -> list:
    return [o for o in obstacles if o.box.intersects(box)]


def _describe(o: Obstacle) -> dict:
    if o.kind == "face":
        return {"kind": "face", "t": o.t, "box_norm": o.box.as_list(), "box": o.source_box}
    return {"kind": "region", "name": o.name, "box_norm": o.box.as_list()}


def plan_placement(document: SubtitleDocument, spec: PlacementSpec) -> PlacementPlan:
    faces, inputs, step = load_face_obstacles(spec)
    inputs["regions"] = [o.name for o in spec.regions]
    return plan_from_obstacles(document, faces, spec.regions, step=step, min_hold=spec.min_hold,
                               max_lines=spec.max_lines, inputs=inputs)


def plan_from_obstacles(document: SubtitleDocument, faces: Sequence[Obstacle], regions: Sequence[Obstacle], *,
                        step: float, min_hold: float, max_lines: int = 2, inputs: Optional[dict] = None,
                        boxes: Optional[dict] = None) -> PlacementPlan:
    boxes = boxes or caption_boxes(max_lines)
    bottom, top = boxes["bottom"], boxes["top"]
    cues = sorted(document.cues, key=lambda c: (c.start, c.end, c.id))
    positions: dict = {}
    causes: dict = {}
    top_blocked: dict = {}
    unavoidable = []
    for cue in cues:
        during = _faces_during(faces, cue.start, cue.end, step)
        during += [r for r in regions if r.start < cue.end and cue.start < r.end]
        at_bottom = _blocking(during, bottom)
        at_top = _blocking(during, top)
        top_blocked[cue.id] = bool(at_top)
        if at_bottom and not at_top:
            positions[cue.id] = "top"
            causes[cue.id] = at_bottom[0]
        else:
            positions[cue.id] = "bottom"
            if at_bottom:
                unavoidable.append({"cue_id": cue.id, "start": cue.start, "end": cue.end,
                                    "bottom": _describe(at_bottom[0]), "top": _describe(at_top[0])})

    # minimum hold: a run of bottom cues between two moved cues that would be on
    # screen (from the previous moved cue's end to the next one's start) for less
    # than min_hold stays at the top with its neighbours
    held = set()
    i = 0
    while i < len(cues):
        if positions[cues[i].id] != "bottom":
            i += 1
            continue
        j = i
        while j < len(cues) and positions[cues[j].id] == "bottom":
            j += 1
        if i > 0 and j < len(cues):
            span = cues[j].start - cues[i - 1].end
            run = cues[i:j]
            if span < min_hold and not any(top_blocked[c.id] for c in run):
                for c in run:
                    positions[c.id] = "top"
                    held.add(c.id)
        i = j

    moves = []
    for cue in cues:
        if positions[cue.id] != "top":
            continue
        move = {"cue_id": cue.id, "start": cue.start, "end": cue.end, "to": "top"}
        if cue.id in held:
            move["reason"] = "min_hold"
        else:
            cause = causes[cue.id]
            move["reason"] = cause.kind
            move["cause"] = _describe(cause)
        moves.append(move)
    return PlacementPlan(positions, moves, unavoidable, dict(inputs or {}))
