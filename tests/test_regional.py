"""Roadmap step 10: regional caption profiles, `ja-broadcast` first (docs/adr/ADR-008-regional-profiles.md)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats import GENERATORS, generate_srt, generate_vtt, parse_ttml, validate_ttml
from subtitle_skill.formats.ass import generate_ass
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.operations import execute
from subtitle_skill.regional import PROFILES, apply_profile

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "regional" / "ja_two_speakers_ruby.json").read_text(encoding="utf-8"))
VENDOR_ROOT = FIXTURES / "ffmpeg_skill_vendor"
JA = PROFILES["ja-broadcast"]


def _doc():
    return SubtitleDocument.from_dict(FIXTURE)


def _generate(tmp_path, fmt, profile=None, name="out"):
    request = {"operation": "generate", "workspace": str(tmp_path), "format": fmt, "output_path": f"{name}.{fmt}",
               "subtitle": FIXTURE}
    if profile:
        request["regional_profile"] = profile
    response = execute(request)
    return response, (tmp_path / f"{name}.{fmt}").read_text(encoding="utf-8")


# --- the model --------------------------------------------------------------


def test_ruby_is_part_of_the_cue():
    cue = _doc().cues[0]
    assert [r.to_dict() for r in cue.ruby] == [{"base": "字幕", "text": "じまく"}]
    assert cue.ruby_segments() == [("今日は", None), ("字幕", "じまく"), ("の話です", None)]
    assert cue.to_dict()["ruby"] == [{"base": "字幕", "text": "じまく"}]


@pytest.mark.parametrize("ruby", [[], [{"base": "無い", "text": "x"}], [{"base": "字幕", "text": ""}],
                                  [{"base": "字幕", "text": "じまく", "extra": 1}]])
def test_bad_ruby_is_refused(ruby):
    with pytest.raises(SubtitleSkillError):
        SubtitleDocument.from_dict({"id": "d", "language": "ja", "cues": [
            {"id": "1", "start": 0, "end": 1, "text": "字幕", "ruby": ruby}]})


# --- the profile -------------------------------------------------------------


def test_ja_broadcast_colours_speakers_in_order_of_appearance():
    document, colors = apply_profile(JA, _doc())
    assert colors == {"佐藤": "FFFFFF", "田中": "FFFF00"}
    assert [c.style.color for c in document.cues] == ["FFFFFF", "FFFF00", "FFFFFF"]
    # the colour identifies the speaker: no name is drawn, it is kept in metadata
    assert all(c.speaker is None for c in document.cues)
    assert document.cues[1].metadata["speaker"] == "田中"


def test_profile_is_only_for_its_languages():
    with pytest.raises(SubtitleSkillError) as exc:
        apply_profile(JA, SubtitleDocument.from_dict({**FIXTURE, "language": "en"}))
    assert exc.value.code == "INVALID_INPUT"
    with pytest.raises(SubtitleSkillError):
        execute({"operation": "generate", "workspace": "/tmp", "format": "srt", "output_path": "x.srt",
                 "subtitle": FIXTURE, "regional_profile": "fr-broadcast"})


def test_profile_line_limits_apply_when_the_caller_sets_none(tmp_path):
    long = {**FIXTURE, "cues": [{"id": "1", "start": 0, "end": 6, "text": "あ" * 16, "speaker": "佐藤"}]}
    without = execute({"operation": "generate", "workspace": str(tmp_path), "format": "ttml", "output_path": "a.ttml",
                       "subtitle": long})
    with_profile = execute({"operation": "generate", "workspace": str(tmp_path), "format": "ttml",
                            "output_path": "b.ttml", "subtitle": long, "regional_profile": "ja-broadcast"})
    # 16 full-width characters: over ja-broadcast's 15, and over the ja locale default (13) too;
    # the limit named in the message is the profile's
    assert any("15" in o["message"] for o in with_profile["observation"] if o["code"] == "LINE_TOO_LONG")
    assert any("13" in o["message"] for o in without["observation"] if o["code"] == "LINE_TOO_LONG")


# --- TTML carries colours and ruby ---------------------------------------------


def test_ttml_with_the_profile_carries_colours_and_ruby(tmp_path):
    response, ttml = _generate(tmp_path, "ttml", "ja-broadcast")
    assert response["regional_profile"] == {"name": "ja-broadcast", "speaker_colors": {"佐藤": "FFFFFF", "田中": "FFFF00"},
                                            "colors_carried": True}
    assert 'tts:color="#ffff00"' in ttml and 'tts:color="#ffffff"' in ttml
    assert '<span tts:ruby="container"><span tts:ruby="base">字幕</span><span tts:ruby="text">じまく</span></span>' in ttml
    assert "佐藤" not in ttml and "田中" not in ttml
    assert validate_ttml(ttml, profile="ttml") == []
    back = parse_ttml(ttml)
    assert back.cues[0].text == "今日は字幕の話です"
    assert [r.to_dict() for r in back.cues[0].ruby] == [{"base": "字幕", "text": "じまく"}]
    assert back.cues[1].style.color == "FFFF00"


def test_ttml_without_a_profile_uses_the_international_defaults(tmp_path):
    response, ttml = _generate(tmp_path, "ttml")
    assert "regional_profile" not in response
    assert "tts:color=\"#ffff00\"" not in ttml and "s_span" not in ttml  # no per-speaker colour
    assert "佐藤: " in ttml and "田中: " in ttml  # names as a prefix
    assert 'tts:ruby="container"' in ttml  # ruby is content, not a profile feature


@pytest.mark.parametrize("profile", [None, "ja-broadcast"])
def test_ttml_with_ruby_and_colours_validates_against_the_schema(tmp_path, profile):
    etree = pytest.importorskip("lxml.etree")
    schema = etree.XMLSchema(etree.parse(str(FIXTURES / "schemas" / "ttml2" / "ttml2.xsd")))
    _response, ttml = _generate(tmp_path, "ttml", profile)
    assert schema.validate(etree.fromstring(ttml.encode("utf-8"))), [str(e) for e in schema.error_log]


def test_ebu_tt_d_has_no_ruby_so_the_reading_is_bracketed(tmp_path):
    response, ttml = _generate(tmp_path, "ebu-tt-d", "ja-broadcast")
    assert "字幕（じまく）" in ttml and "tts:ruby" not in ttml
    assert 'tts:color="#ffff00"' in ttml
    assert validate_ttml(ttml, profile="ebu-tt-d") == []
    assert [o["code"] for o in response["observation"]].count("RUBY_AS_TEXT") == 1


def test_srt_and_webvtt_keep_names_and_draw_no_colour(tmp_path):
    response, srt = _generate(tmp_path, "srt", "ja-broadcast")
    assert response["regional_profile"]["colors_carried"] is False
    assert "佐藤: 今日は字幕（じまく）の話です" in srt
    _response, vtt = _generate(tmp_path, "vtt", "ja-broadcast")
    assert "佐藤: 今日は<ruby>字幕<rt>じまく</rt></ruby>の話です" in vtt


def test_vertical_text_in_ttml_and_webvtt_not_burned():
    doc = SubtitleDocument.from_dict({"id": "v", "language": "ja", "cues": [
        {"id": "1", "start": 0, "end": 2, "text": "縦書き", "style": {"vertical": True}}]})
    ttml = GENERATORS["ttml"](doc)
    assert 'tts:writingMode="tbrl"' in ttml and validate_ttml(ttml, profile="ttml") == []
    assert parse_ttml(ttml).cues[0].style.vertical is True
    assert "vertical:rl" in generate_vtt(doc)
    with pytest.raises(SubtitleSkillError) as exc:
        generate_srt(doc)
    assert exc.value.code == "UNSUPPORTED_FORMAT"
    with pytest.raises(SubtitleSkillError):
        generate_ass(doc, width=1280, height=720)


# --- burn-in ---------------------------------------------------------------------


def test_ass_draws_speaker_colours_and_ruby_over_its_base():
    document, _ = apply_profile(JA, _doc())
    ass = generate_ass(document, width=1280, height=720)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    line1 = next(e for e in events if e.endswith("今日は字幕の話です"))
    ruby = next(e for e in events if e.endswith("じまく"))
    assert "\\c&HFFFFFF&" in line1 and "\\fs30" in ruby
    assert any(e.endswith("よろしくお願いします") and "\\c&H00FFFF&" in e for e in events)  # yellow as BGR
    # nine full-width characters centred on x 640; the base 字幕 is characters 4-5,
    # so its centre is half a character (0.5 x 60 px / 1.448) left of the line's
    x_line = int(line1.split("\\pos(")[1].split(",")[0])
    x_ruby, y_ruby = (int(v) for v in ruby.split("\\pos(")[1].split(")")[0].split(","))
    y_line = int(line1.split("\\pos(")[1].split(")")[0].split(",")[1])
    assert x_ruby == pytest.approx(x_line - 0.5 * 60 / 1.448, abs=1) and y_ruby < y_line
    plain = generate_ass(_doc(), width=1280, height=720)
    assert "佐藤: 今日は字幕の話です" in plain and "\\c&H00FFFF&" not in plain


@pytest.fixture
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _rgb_frame(path, t, width, height):
    data = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    assert len(data) == width * height * 3
    return data


def _count(frame, pred):
    return sum(1 for i in range(0, len(frame), 3) if pred(frame[i], frame[i + 1], frame[i + 2]))


def _yellow(r, g, b):
    return r > 200 and g > 200 and b < 90


def _white(r, g, b):
    return r > 200 and g > 200 and b > 200


def _ink_rows(frame, width, pred):
    rows = set()
    for i in range(0, len(frame), 3):
        if pred(frame[i], frame[i + 1], frame[i + 2]):
            rows.add((i // 3) // width)
    return rows


@pytest.mark.parametrize("profile", ["ja-broadcast", None])
def test_burn_with_and_without_the_profile(tmp_path, ffmpeg_skill_install, profile):
    if not shutil.which("ffmpeg"):
        pytest.skip("real ffmpeg is required for render tests")
    width, height = 640, 360
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={width}x{height}:d=8:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "in.mp4")], check=True, capture_output=True)
    request = {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
               "video_input": "in.mp4", "subtitle": FIXTURE}
    if profile:
        request["regional_profile"] = profile
    response = execute(request)
    sidecar = json.loads((tmp_path / "out.mp4.subtitle-skill.json").read_text(encoding="utf-8"))
    assert any("ass=" in c for c in sidecar["engine_response"]["commands"])

    speaker2 = _rgb_frame(tmp_path / "out.mp4", 4.0, width, height)
    speaker1 = _rgb_frame(tmp_path / "out.mp4", 1.5, width, height)
    if profile:
        assert response["regional_profile"]["speaker_colors"] == {"佐藤": "FFFFFF", "田中": "FFFF00"}
        assert _count(speaker2, _yellow) > 50  # the second speaker is yellow
        assert _count(speaker1, _yellow) == 0
    else:
        assert "regional_profile" not in response
        assert _count(speaker2, _yellow) == 0  # international default: white for everyone
        assert _count(speaker2, _white) > 50
    # ruby: ink in a band above the cue's line, only while the ruby cue is on screen
    rows1 = _ink_rows(speaker1, width, _white)
    rows3 = _ink_rows(_rgb_frame(tmp_path / "out.mp4", 6.5, width, height), width, _white)
    assert min(rows1) < min(rows3) - 8
