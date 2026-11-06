"""Roadmap step 8: captions placed away from faces (docs/adr/ADR-006-caption-placement.md)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats.ass import escape_text, generate_ass
from subtitle_skill.formats.srt import generate_srt
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.pathpolicy import PathPolicy
from subtitle_skill.placement import (
    Box,
    Obstacle,
    caption_boxes,
    parse_placement,
    plan_from_obstacles,
    plan_placement,
)

FIXTURES = Path(__file__).parent / "fixtures" / "placement"
VENDOR_ROOT = Path(__file__).parent / "fixtures" / "ffmpeg_skill_vendor"


def _doc(cues, language="en"):
    return SubtitleDocument.from_dict({"id": "d", "language": language, "cues": cues})


def _ten_cues():
    # one cue a second, 0.9 s long: c0 [0, 0.9) ... c9 [9, 9.9)
    return _doc([{"id": f"c{i}", "start": i, "end": i + 0.9, "text": f"line {i}"} for i in range(10)])


def _workspace(tmp_path):
    shutil.copy(FIXTURES / "faces_low_2_5s.json", tmp_path / "faces.json")
    return PathPolicy(str(tmp_path))


def test_face_low_for_2_to_5_s_moves_only_those_cues(tmp_path):
    policy = _workspace(tmp_path)
    plan = plan_placement(_ten_cues(), parse_placement({"faces": "faces.json"}, policy))
    assert plan.top_cue_ids() == {"c2", "c3", "c4"}
    assert [m["cue_id"] for m in plan.moves] == ["c2", "c3", "c4"]
    for move in plan.moves:
        assert move["reason"] == "face"
        # each move names the face box that caused it, in source pixels and as fractions
        assert move["cause"]["box"] == [260.0, 250.0, 120.0, 100.0]
        assert move["start"] <= move["cause"]["t"] < move["end"]
    assert plan.unavoidable == []
    assert plan.inputs["faces"]["file"] == "faces.json"
    assert len(plan.inputs["faces"]["sha256"]) == 64


def test_one_decision_per_cue_never_mid_cue(tmp_path):
    # a cue spanning the face's arrival (1.5 - 2.5 s) moves as a whole
    policy = _workspace(tmp_path)
    doc = _doc([{"id": "a", "start": 0.0, "end": 1.4, "text": "a"},
                {"id": "b", "start": 1.5, "end": 2.5, "text": "b"}])
    plan = plan_placement(doc, parse_placement({"faces": "faces.json"}, policy))
    assert plan.positions == {"a": "bottom", "b": "top"}


def test_min_hold_keeps_a_short_bottom_run_at_the_top():
    low = Box(0.4, 0.75, 0.2, 0.2)
    faces = [Obstacle("face", low, t, t, t=t, source_box=[1, 2, 3, 4]) for t in (0.5, 2.5)]
    doc = _doc([
        {"id": "a", "start": 0.0, "end": 1.0, "text": "a"},
        {"id": "b", "start": 1.2, "end": 1.8, "text": "b"},  # no face; 1.0 s between a and c
        {"id": "c", "start": 2.0, "end": 3.0, "text": "c"},
    ])
    held = plan_from_obstacles(doc, faces, [], step=0.0, min_hold=1.5)
    assert held.positions == {"a": "top", "b": "top", "c": "top"}
    assert [m["reason"] for m in held.moves] == ["face", "min_hold", "face"]
    free = plan_from_obstacles(doc, faces, [], step=0.0, min_hold=0.5)
    assert free.positions["b"] == "bottom"


def test_named_region_is_avoided_like_a_face(tmp_path):
    policy = PathPolicy(str(tmp_path))
    spec = parse_placement(
        {"avoid": [{"name": "lower-third", "box": [0.0, 0.7, 1.0, 0.2], "start": 3.0, "end": 6.0}]}, policy
    )
    plan = plan_placement(_ten_cues(), spec)
    assert plan.top_cue_ids() == {"c3", "c4", "c5"}
    assert plan.moves[0]["cause"] == {"kind": "region", "name": "lower-third", "box_norm": [0.0, 0.7, 1.0, 0.2]}


def test_both_boxes_blocked_keeps_the_cue_down_and_reports_it():
    tall = Box(0.0, 0.0, 1.0, 1.0)
    plan = plan_from_obstacles(_doc([{"id": "a", "start": 0, "end": 1, "text": "a"}]),
                               [], [Obstacle("region", tall, 0, 10, name="sign-language")], step=0, min_hold=1)
    assert plan.positions == {"a": "bottom"}
    assert plan.unavoidable[0]["cue_id"] == "a"


def test_reframe_maps_face_boxes_into_the_crop(tmp_path):
    # the 9:16 crop [400, 0, 202, 360] of the 640x360 source: the low face (x 260-380)
    # is outside it, so nothing moves after the crop
    policy = _workspace(tmp_path)
    reframe = {"schema": "detection-skill/reframe@1", "frame_size": {"width": 640, "height": 360},
               "shots": [{"index": 0, "start": 0.0, "end": 10.0, "x": 400, "y": 0, "width": 202, "height": 360}]}
    (tmp_path / "reframe.json").write_text(json.dumps(reframe), encoding="utf-8")
    plan = plan_placement(_ten_cues(), parse_placement({"faces": "faces.json", "reframe": "reframe.json"}, policy))
    assert plan.top_cue_ids() == frozenset()
    # a crop that contains the face: the same three cues move
    reframe["shots"][0]["x"] = 220
    (tmp_path / "reframe.json").write_text(json.dumps(reframe), encoding="utf-8")
    plan = plan_placement(_ten_cues(), parse_placement({"faces": "faces.json", "reframe": "reframe.json"}, policy))
    assert plan.top_cue_ids() == {"c2", "c3", "c4"}
    assert plan.inputs["reframe"]["windows"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"faces": 3},
        {"faces": "faces.json", "bogus": 1},
        {"avoid": [{"name": "x", "box": [0.5, 0.5, 0.6, 0.1]}]},
        {"avoid": [{"box": [0, 0, 0.1, 0.1]}]},
        {"faces": "faces.json", "min_hold": -1},
        {"reframe": "faces.json"},
    ],
)
def test_bad_placement_is_refused(tmp_path, raw):
    policy = _workspace(tmp_path)
    with pytest.raises(SubtitleSkillError):
        parse_placement(raw, policy)


def test_faces_must_be_a_face_detections_document(tmp_path):
    policy = PathPolicy(str(tmp_path))
    (tmp_path / "f.json").write_text(json.dumps({"schema": "detection-skill/detections@1", "kind": "text"}))
    with pytest.raises(SubtitleSkillError) as exc:
        plan_placement(_ten_cues(), parse_placement({"faces": "f.json"}, policy))
    assert exc.value.code == "INVALID_INPUT"


def test_ass_has_per_event_alignment_and_escapes_text():
    doc = _doc([{"id": "a", "start": 0, "end": 1, "text": "{x}\\N"}, {"id": "b", "start": 1, "end": 2, "text": "b"}])
    ass = generate_ass(doc, width=640, height=360, positions={"b": "top"})
    assert "PlayResX: 640" in ass and "PlayResY: 360" in ass
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events[0].endswith("," + escape_text("{x}\\N"))
    assert "\\{x\\}" in events[0]
    assert events[1].endswith(",{\\an8}b")
    # caption.py's defaults scaled to 360 lines: 24 pt -> 30 px, margin 30 -> 38 px
    style = next(line for line in ass.splitlines() if line.startswith("Style: Default"))
    assert ",30,&H00FFFFFF," in style and style.endswith(",2,32,32,38,1")


def test_soft_track_gets_an8_on_moved_cues():
    srt = generate_srt(_doc([{"id": "a", "start": 0, "end": 1, "text": "a"}, {"id": "b", "start": 1, "end": 2, "text": "b"}]),
                       top_cue_ids=frozenset({"b"}))
    assert "\n{\\an8}b\n" in srt and "\na\n" in srt


def test_caption_boxes_follow_caption_py_defaults():
    boxes = caption_boxes(2)
    assert boxes["top"].y == pytest.approx(30 / 288)
    assert boxes["bottom"].y + boxes["bottom"].h == pytest.approx(1 - 30 / 288)


# --- burned end to end through the vendored ffmpeg-skill caption.py ----------


@pytest.fixture
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _gray_frame(path, t, width, height):
    proc = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    )
    data = proc.stdout
    assert len(data) == width * height
    return data


def _bright_rows(frame, width, y0, y1):
    return sum(1 for y in range(y0, y1) if max(frame[y * width:(y + 1) * width]) > 200)


def test_render_burns_moved_cues_at_the_top(tmp_path, ffmpeg_skill_install):
    if not shutil.which("ffmpeg"):
        pytest.skip("real ffmpeg is required for render tests")
    from subtitle_skill.operations import execute

    width, height = 640, 360
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={width}x{height}:d=10:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "in.mp4")],
        check=True, capture_output=True,
    )
    shutil.copy(FIXTURES / "faces_low_2_5s.json", tmp_path / "faces.json")
    request = {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": _ten_cues().to_dict(),
        "placement": {"faces": "faces.json"},
    }
    response = execute(request)
    assert response["status"] == "ok"
    assert response["placement"]["top_cues"] == ["c2", "c3", "c4"]
    sidecar = json.loads((tmp_path / "out.mp4.subtitle-skill.json").read_text())
    assert sidecar["placement"]["moves"][0]["cause"]["box"] == [260.0, 250.0, 120.0, 100.0]
    # burned from subtitle-skill's ASS (per-event alignment), not caption.py's SRT force_style
    commands = sidecar["engine_response"]["commands"]
    assert any("ass=" in c and ".subtitle-skill-src.ass" in c for c in commands)
    assert not any("force_style" in c for c in commands)

    out = tmp_path / "out.mp4"
    top = (0, height // 2)
    bottom = (height // 2, height)
    at_top = _gray_frame(out, 3.4, width, height)  # c3: moved
    assert _bright_rows(at_top, width, *top) > 5 and _bright_rows(at_top, width, *bottom) == 0
    at_bottom = _gray_frame(out, 6.4, width, height)  # c6: not moved
    assert _bright_rows(at_bottom, width, *bottom) > 5 and _bright_rows(at_bottom, width, *top) == 0

    # a second run with the same inputs is reused; changing the faces file is not
    assert execute(request)["reused"] is True


def test_render_mux_with_placement_tags_moved_cues(tmp_path, ffmpeg_skill_install):
    if not shutil.which("ffmpeg"):
        pytest.skip("real ffmpeg is required for render tests")
    from subtitle_skill.operations import execute

    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=10:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "in.mp4")],
        check=True, capture_output=True,
    )
    shutil.copy(FIXTURES / "faces_low_2_5s.json", tmp_path / "faces.json")
    response = execute({
        "operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mkv",
        "video_input": "in.mp4", "subtitle": _ten_cues().to_dict(), "mode": "mux",
        "placement": {"faces": "faces.json"},
    })
    assert response["placement"]["top_cues"] == ["c2", "c3", "c4"]
    proc = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-i", str(tmp_path / "out.mkv"), "-map", "0:s:0", "-f", "srt", "-"],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.count("{\\an8}") == 3
