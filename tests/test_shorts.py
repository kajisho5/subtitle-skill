"""Roadmap step 11: the `shorts` caption profile (docs/adr/ADR-009-shorts-captions.md)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats.ass import generate_shorts_ass
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.operations import execute
from subtitle_skill.shorts import PLATFORM_SAFE, SAFE, ShortsLayout, chunk_document, estimate_width

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "shorts" / "vertical_words_en.json").read_text(encoding="utf-8"))
VENDOR_ROOT = FIXTURES / "ffmpeg_skill_vendor"
W, H = 360, 640


def _doc(data=FIXTURE):
    return SubtitleDocument.from_dict(data)


def test_safe_zone_is_inside_every_9x16_destination():
    for zone in PLATFORM_SAFE.values():
        for edge in ("top", "bottom", "left", "right"):
            assert SAFE[edge] >= zone[edge]
    assert SAFE == {"top": 0.10, "bottom": 0.22, "left": 0.05, "right": 0.14}


def test_chunks_are_one_or_two_words_from_word_timings():
    chunks = chunk_document(_doc())
    assert [c.text for c in chunks.cues] == [
        "Welcome back,", "everyone to", "the channel", "Today:", "unbelievably extraordinary", "results!", "Let's go",
    ]
    assert all(1 <= len(c.words) <= 2 for c in chunks.cues)
    first = chunks.cues[0]
    assert (first.id, first.start, first.metadata["source_cue"]) == ("1.1", 0.0, "1")
    # held to the next chunk when the gap is short; a name prefix would be a third word
    assert first.end == 0.75 and first.speaker is None and first.metadata["speaker"] == "Host"
    assert chunks.cues[-1].end == 5.2  # the last chunk ends with its last word


def test_words_are_validated():
    bad = {**FIXTURE, "cues": [{**FIXTURE["cues"][2], "words": [{"text": "x", "start": 9.0, "end": 9.5}]}]}
    with pytest.raises(SubtitleSkillError) as exc:
        _doc(bad)
    assert exc.value.code == "INVALID_TIME_RANGE"
    missing = {**FIXTURE, "cues": [{k: v for k, v in FIXTURE["cues"][2].items() if k != "words"}]}
    with pytest.raises(SubtitleSkillError) as exc:
        chunk_document(_doc(missing))
    assert exc.value.code == "MISSING_INPUT"


def _events(ass):
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            tags, text = line.split("}", 1)
            x, y = (float(v) for v in tags.split("\\pos(")[1].split(")")[0].split(","))
            size = int(tags.split("\\fs")[1].split("\\")[0].split("}")[0])
            out.append((x, y, size, text))
    return out


@pytest.mark.parametrize("width, height", [(360, 640), (1080, 1920)])
def test_every_cue_box_sits_inside_the_safe_zone(width, height):
    events = _events(generate_shorts_ass(chunk_document(_doc()), width=width, height=height))
    assert len(events) == 7
    left, right = SAFE["left"] * width, (1 - SAFE["right"]) * width
    top, bottom = SAFE["top"] * height, (1 - SAFE["bottom"]) * height
    for x, y, size, text in events:
        half = estimate_width(text, size) / 2
        assert left <= x - half and x + half <= right, text
        assert top <= y - size and y <= bottom, text
        assert len(text.split()) <= 2
    # large type: 5.5 % of the height, smaller only for the chunk that would not fit
    sizes = {text: size for _x, _y, size, text in events}
    assert sizes["Let's go"] == ShortsLayout(width, height).font_size
    assert sizes["unbelievably extraordinary"] < sizes["Let's go"]


@pytest.fixture
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _video(path, width=W, height=H, duration=6):
    if not shutil.which("ffmpeg"):
        pytest.skip("real ffmpeg is required for render tests")
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={width}x{height}:d={duration}:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _ink_box(path, t):
    data = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    assert len(data) == W * H
    xs = [i % W for i, v in enumerate(data) if v > 200]
    ys = [i // W for i, v in enumerate(data) if v > 200]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _request(tmp_path, **extra):
    return {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
            "video_input": "in.mp4", "subtitle": FIXTURE, "caption_profile": "shorts", **extra}


def test_burned_9x16_cues_sit_inside_the_safe_zone(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    response = execute(_request(tmp_path))
    cues = response["caption_profile"]["cues"]
    assert len(cues) == 7 and all(c["words"] <= 2 for c in cues)
    left, right = SAFE["left"] * W, (1 - SAFE["right"]) * W
    top, bottom = SAFE["top"] * H, (1 - SAFE["bottom"]) * H
    for cue in cues:
        box = _ink_box(tmp_path / "out.mp4", (cue["start"] + cue["end"]) / 2)
        assert box is not None, cue
        x0, y0, x1, y1 = box
        assert left <= x0 and x1 <= right and top <= y0 and y1 <= bottom, (cue["text"], box)


def test_the_speakers_face_is_avoided(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    shutil.copy(FIXTURES / "shorts" / "faces_9x16.json", tmp_path / "faces.json")
    response = execute(_request(tmp_path, placement={"faces": "faces.json"}))
    # the face is low from the 4.0 s sample: "results!" (held on screen to 4.2 s) and "Let's go"
    assert response["placement"]["top_cues"] == ["2.3", "3.1"]
    _x0, y0, _x1, y1 = _ink_box(tmp_path / "out.mp4", 4.6)
    assert SAFE["top"] * H <= y0 and y1 < H / 3
    _x0, y0, _x1, _y1 = _ink_box(tmp_path / "out.mp4", 0.3)
    assert y0 > H / 2


def test_shorts_is_refused_where_it_does_not_apply(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4", width=640, height=360)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path))
    assert exc.value.code == "INVALID_INPUT" and "vertical" in exc.value.message
    with pytest.raises(SubtitleSkillError):
        execute(_request(tmp_path, mode="mux"))
    with pytest.raises(SubtitleSkillError):
        execute(_request(tmp_path, caption_profile="reels-xl"))
