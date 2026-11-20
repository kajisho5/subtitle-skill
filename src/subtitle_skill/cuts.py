"""Cut-aware cues (docs/adr/ADR-013-caption-quality.md, part 1).

`render` `cuts` (opt-in): no cue, and no `shorts` chunk, stays on screen across a hard
cut. The cut times come either from a media-analysis-skill `scene_detection` result
(`cuts.shots`, a workspace path to its `media-analysis/response@1` or to the one
observation) or from ffmpeg's `scdet` filter run here on the video (`cuts.detect: true`,
the same filter and default threshold media-analysis uses).

A cue that a cut falls inside (more than `EDGE` seconds from either end) is changed by one
of three moves, chosen so that every cue that is left is at least `min_duration` long:

- **split**: both sides would be at least `min_duration` and the text can be divided:
  the cue becomes two cues that meet at the cut. With word timings the words spoken before
  the cut go first; without them the text is divided at the word boundary whose share of
  characters is nearest the time share. A no-break space never divides (a number written
  by `spoken_numbers` stays one token).
- **start moved**: the part before the cut is too short: the cue starts at the cut.
- **end moved**: the part after the cut is too short: the cue ends at the cut.

A cue too short for any of them (both sides under `min_duration`) is left as it is and
reported in `unresolved` (observation `CUE_SPANS_CUT`). Every change is reported.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .errors import SubtitleSkillError
from .models import SubtitleCue, SubtitleDocument, WordTiming

CUTS_FIELDS = frozenset({"shots", "detect", "threshold", "min_scene", "min_duration"})
SHOTS_KIND = "scene_detection"
RESPONSE_SCHEMA = "media-analysis/response@1"
DEFAULT_THRESHOLD = 10.0  # scdet score 0-100, media-analysis-skill's default
DEFAULT_MIN_SCENE = 0.5  # seconds; cuts closer than this to the previous one are ignored
#: a cut this close to a cue's start or end is the cue's own boundary, not inside it
EDGE = 0.04
_SPACE = re.compile(r"[ \t\n]+")


@dataclass(frozen=True)
class CutSpec:
    shots_path: Optional[Path]
    shots_rel: Optional[str]
    detect: bool
    threshold: float
    min_scene: float
    min_duration: Optional[float]
    raw: Mapping[str, Any]


def _number(raw: Any, field: str, low: float, high: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not low <= raw <= high:
        raise SubtitleSkillError("INVALID_INPUT", f"cuts.{field} must be a number in [{low}, {high}], got {raw!r}",
                                 details={"field": f"cuts.{field}"})
    return float(raw)


def parse_cuts(raw: Any, policy) -> CutSpec:
    if not isinstance(raw, Mapping):
        raise SubtitleSkillError("INVALID_INPUT", "cuts must be an object", details={"field": "cuts"})
    unknown = set(raw) - CUTS_FIELDS
    if unknown:
        raise SubtitleSkillError("INVALID_INPUT", f"cuts: unknown field(s) {sorted(unknown)}",
                                 details={"field": "cuts"})
    shots = raw.get("shots")
    detect = raw.get("detect", False)
    if not isinstance(detect, bool):
        raise SubtitleSkillError("INVALID_INPUT", "cuts.detect must be true or false", details={"field": "cuts.detect"})
    if (shots is None) == (not detect):
        raise SubtitleSkillError(
            "INVALID_INPUT", "cuts needs exactly one source: `shots` (a media-analysis scene_detection document) "
            "or `detect: true` (measure the cuts with ffmpeg scdet)", details={"field": "cuts"})
    shots_path = None
    if shots is not None:
        if not isinstance(shots, str) or not shots:
            raise SubtitleSkillError("INVALID_INPUT", "cuts.shots must be a path inside the workspace",
                                     details={"field": "cuts.shots"})
        shots_path = policy.resolve_input(shots)
    for name in ("threshold", "min_scene"):
        if name in raw and not detect:
            raise SubtitleSkillError("INVALID_INPUT", f"cuts.{name} applies to `detect: true` only",
                                     details={"field": f"cuts.{name}"})
    return CutSpec(
        shots_path=shots_path,
        shots_rel=shots,
        detect=detect,
        threshold=_number(raw["threshold"], "threshold", 0.0, 100.0) if "threshold" in raw else DEFAULT_THRESHOLD,
        min_scene=_number(raw["min_scene"], "min_scene", 0.0, 60.0) if "min_scene" in raw else DEFAULT_MIN_SCENE,
        min_duration=_number(raw["min_duration"], "min_duration", 0.0, 10.0) if "min_duration" in raw else None,
        raw=dict(raw),
    )


def _observation(doc: Any) -> Mapping[str, Any]:
    if isinstance(doc, Mapping) and doc.get("schema") == RESPONSE_SCHEMA:
        for result in doc.get("results") or []:
            if isinstance(result, Mapping) and result.get("kind") == SHOTS_KIND and result.get("status") == "ok":
                obs = result.get("observation")
                if isinstance(obs, Mapping):
                    return obs
        raise SubtitleSkillError("INVALID_INPUT", f"cuts.shots: the {RESPONSE_SCHEMA} has no ok {SHOTS_KIND} result",
                                 details={"field": "cuts.shots"})
    if isinstance(doc, Mapping) and doc.get("kind") == SHOTS_KIND and isinstance(doc.get("data"), Mapping):
        return doc
    raise SubtitleSkillError(
        "INVALID_INPUT",
        f"cuts.shots must be a media-analysis {RESPONSE_SCHEMA} with a {SHOTS_KIND} result, or that observation",
        details={"field": "cuts.shots"})


def read_shots(path: Path, video_sha256: str) -> List[float]:
    """Cut times (seconds) from a media-analysis scene_detection result: each scene's start
    after the first (the scenes honour its `min_scene_duration`), or its `cuts` when it has no
    scenes. A result measured on another file (its `asset.fingerprint`) is refused."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SubtitleSkillError("INVALID_INPUT", f"cuts.shots is not readable JSON: {exc}",
                                 details={"field": "cuts.shots"}) from exc
    obs = _observation(doc)
    fingerprint = (obs.get("asset") or {}).get("fingerprint") if isinstance(obs.get("asset"), Mapping) else None
    if isinstance(fingerprint, str) and fingerprint and fingerprint != video_sha256:
        raise SubtitleSkillError(
            "INVALID_INPUT", "cuts.shots was measured on another file (asset.fingerprint is not this video's sha256)",
            details={"field": "cuts.shots"})
    data = obs["data"]
    times: List[float] = []
    scenes = data.get("scenes")
    if isinstance(scenes, list) and scenes:
        for scene in scenes[1:]:
            if isinstance(scene, Mapping) and isinstance(scene.get("start"), (int, float)):
                times.append(float(scene["start"]))
    else:
        for cut in data.get("cuts") or []:
            if isinstance(cut, Mapping) and isinstance(cut.get("time"), (int, float)):
                times.append(float(cut["time"]))
    return sorted(t for t in set(times) if t > 0)


_FRAME = re.compile(r"^frame:(\d+)\s+pts:(-?\d+)\s+pts_time:(-?[0-9.]+)")
_KV = re.compile(r"^lavfi\.scd\.(\w+)=(-?[0-9.]+)")


def detect_cuts(video: Path, *, threshold: float, min_scene: float, timeout: int = 600) -> List[float]:
    """Hard cuts measured with ffmpeg's scdet filter (decode only, nothing is written)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SubtitleSkillError("DEPENDENCY_ERROR", "cuts.detect needs ffmpeg on PATH")
    argv = [ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-map", "0:V:0", "-an", "-sn",
            "-vf", f"scdet=threshold={threshold:g},metadata=print:file=-", "-f", "null", "-"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"ffmpeg scdet timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"ffmpeg scdet failed: {proc.stderr.strip()[-500:]}")
    times: List[float] = []
    current = None
    for line in proc.stdout.splitlines():
        m = _FRAME.match(line)
        if m:
            current = float(m.group(3))
            continue
        k = _KV.match(line)
        if k and k.group(1) == "time" and current is not None and current > 0:
            if not times or current - times[-1] >= min_scene:
                times.append(round(current, 3))
    return times


# --- applying the cuts --------------------------------------------------------------------


def _tokens(text: str) -> List[str]:
    return [t for t in _SPACE.split(text) if t]


def _clamp_words(words: Sequence[WordTiming], start: float, end: float) -> tuple:
    out = []
    for w in words:
        s = min(max(w.start, start), end)
        e = min(max(w.end, s), end)
        out.append(WordTiming(w.text, s, e))
    return tuple(out)


def _split_text(cue: SubtitleCue, cut: float) -> Optional[Tuple[str, str, tuple, tuple]]:
    """(first text, second text, first words, second words) divided at `cut`, or None."""
    tokens = _tokens(cue.text)
    if len(tokens) < 2:
        return None
    if cue.words:
        before = [w for w in cue.words if (w.start + w.end) / 2.0 < cut]
        k = len(before)
        if k == 0 or k == len(cue.words):
            return None
        first_words = _clamp_words(cue.words[:k], cue.start, cut)
        second_words = _clamp_words(cue.words[k:], cut, cue.end)
        if len(tokens) == len(cue.words):
            return " ".join(tokens[:k]), " ".join(tokens[k:]), first_words, second_words
        # the text and the words are tokenised differently: divide the text by the words' text
        return " ".join(w.text for w in cue.words[:k]), " ".join(w.text for w in cue.words[k:]), \
            first_words, second_words
    share = (cut - cue.start) / (cue.end - cue.start)
    total = sum(len(t) for t in tokens)
    best, best_gap, run = 1, None, 0
    for k in range(1, len(tokens)):
        run += len(tokens[k - 1])
        gap = abs(run / total - share)
        if best_gap is None or gap < best_gap - 1e-9:
            best, best_gap = k, gap
    return " ".join(tokens[:best]), " ".join(tokens[best:]), (), ()


def _free_id(base: str, taken: set) -> str:
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def apply_cuts(document: SubtitleDocument, cuts: Sequence[float], *, min_duration: float) -> Tuple[SubtitleDocument, dict]:
    """The document with no cue spanning a cut, and what was changed."""
    taken = {c.id for c in document.cues}
    changes: list = []
    unresolved: list = []
    out: list = []
    for cue in document.cues:
        pieces = [cue]
        for cut in cuts:
            nxt = []
            for piece in pieces:
                if not (piece.start + EDGE < cut < piece.end - EDGE):
                    nxt.append(piece)
                    continue
                head, tail = cut - piece.start, piece.end - cut
                split = _split_text(piece, cut) if head >= min_duration and tail >= min_duration \
                    and not piece.ruby else None
                if split is not None:
                    t1, t2, w1, w2 = split
                    a = replace(piece, id=_free_id(f"{piece.id}.a", taken), end=cut, text=t1, words=w1,
                                metadata={**piece.metadata, "source_cue": cue.id})
                    b = replace(piece, id=_free_id(f"{piece.id}.b", taken), start=cut, text=t2, words=w2,
                                metadata={**piece.metadata, "source_cue": cue.id})
                    changes.append({"cue_id": piece.id, "action": "split", "cut": cut,
                                    "from": [piece.start, piece.end],
                                    "to": [{"id": c.id, "start": c.start, "end": c.end, "text": c.text}
                                           for c in (a, b)]})
                    nxt.extend([a, b])
                elif tail >= head and tail >= min_duration:
                    moved = replace(piece, start=cut, words=_clamp_words(piece.words, cut, piece.end))
                    changes.append({"cue_id": piece.id, "action": "start_moved", "cut": cut,
                                    "from": [piece.start, piece.end], "to": [moved.start, moved.end]})
                    nxt.append(moved)
                elif head >= min_duration:
                    moved = replace(piece, end=cut, words=_clamp_words(piece.words, piece.start, cut))
                    changes.append({"cue_id": piece.id, "action": "end_moved", "cut": cut,
                                    "from": [piece.start, piece.end], "to": [moved.start, moved.end]})
                    nxt.append(moved)
                else:
                    unresolved.append({"cue_id": piece.id, "cut": cut, "start": piece.start, "end": piece.end,
                                       "reason": f"both sides of the cut are shorter than min_duration "
                                                 f"{min_duration:g} s"})
                    nxt.append(piece)
            pieces = nxt
        out.extend(pieces)
    report = {"cut_times": list(cuts), "min_duration": min_duration, "changes": changes, "unresolved": unresolved}
    return replace(document, cues=tuple(out)), report
