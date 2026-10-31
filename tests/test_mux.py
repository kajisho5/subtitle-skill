"""`mux` (ECOSYSTEM_ROADMAP step 6): several subtitle documents -> one video.

The integration tests run the vendored, real ffmpeg-skill `caption.py`
(tests/fixtures/ffmpeg_skill_vendor, 2.24.0) against a real ffmpeg, the same
way tests/test_engine_render.py does, and read the result back with ffprobe.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.operations import execute

from test_engine_render import VENDOR_ROOT, _ffprobe_streams, _make_tiny_video, ffmpeg_skill_install  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "translation"


def _ja_doc():
    return {
        "id": "ep",
        "language": "ja",
        "cues": [
            # 0.3 s: shorter than caption.py's own 1 s --min-duration default,
            # which must NOT be applied to subtitle-skill's already-timed track
            {"id": "1", "start": 0.1, "end": 0.4, "text": "はい"},
            {"id": "2", "start": 0.6, "end": 1.5, "text": "字幕の\n翻訳です。"},
        ],
    }


def _en_doc():
    return {
        "id": "ep.en",
        "language": "en",
        "cues": [
            {"id": "1", "start": 0.1, "end": 0.4, "text": "Yes"},
            {"id": "2", "start": 0.6, "end": 1.5, "text": "This is a subtitle translation."},
        ],
    }


def _mux_request(tmp_path, output="out.mkv", tracks=None, **over):
    req = {
        "operation": "mux",
        "workspace": str(tmp_path),
        "video_input": "in.mp4",
        "output_path": output,
        "tracks": tracks if tracks is not None else [{"subtitle": _ja_doc()}, {"subtitle": _en_doc()}],
    }
    req.update(over)
    return req


def _subtitle_streams(path):
    return [s for s in _ffprobe_streams(path) if s["codec_type"] == "subtitle"]


def _extract_srt(path, index, dest):
    ffmpeg = shutil.which("ffmpeg")
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(path), "-map", f"0:s:{index}", str(dest)], check=True)
    return dest.read_text(encoding="utf-8")


def test_two_language_mkv_mux(tmp_path, ffmpeg_skill_install):
    _make_tiny_video(tmp_path / "in.mp4", duration=2.0)
    resp = execute(_mux_request(tmp_path, default_language="en", tracks=[
        {"subtitle": _ja_doc(), "title": "日本語"},
        {"subtitle": _en_doc(), "title": "English"},
    ]))
    assert resp["status"] == "ok" and resp["operation"] == "mux" and resp["mode"] == "mux"
    assert resp["engine"] == "ffmpeg-skill"
    assert resp["timeline"]["cue_count"] == 4
    assert [t["language"] for t in resp["tracks"]] == ["ja", "en"]
    assert [t["default"] for t in resp["tracks"]] == [False, True]

    streams = _subtitle_streams(Path(resp["output"]))
    assert [s["codec_name"] for s in streams] == ["subrip", "subrip"]
    assert [s["tags"].get("language") for s in streams] == ["ja", "en"]
    assert [s["tags"].get("title") for s in streams] == ["日本語", "English"]
    assert [s["disposition"]["default"] for s in streams] == [0, 1]
    # video copied, not re-encoded into something else
    video = [s for s in _ffprobe_streams(Path(resp["output"])) if s["codec_type"] == "video"]
    assert len(video) == 1 and video[0]["codec_name"] == "h264"

    # timing passes through unchanged on BOTH tracks -- including the first,
    # the one caption.py would otherwise lengthen to its 1 s minimum
    ja = _extract_srt(Path(resp["output"]), 0, tmp_path / "ja.srt")
    en = _extract_srt(Path(resp["output"]), 1, tmp_path / "en.srt")
    assert "00:00:00,100 --> 00:00:00,400" in ja and "はい" in ja
    assert "00:00:00,600 --> 00:00:01,500" in ja and "字幕の\n翻訳です。" in ja
    assert "00:00:00,100 --> 00:00:00,400" in en and "This is a subtitle translation." in en
    assert not list(tmp_path.glob("*_adjusted.srt"))
    assert "TRACK_RELAID_OUT" not in [o["code"] for o in resp["observation"]]
    assert not list(tmp_path.glob("*.subtitle-skill-src.*"))


def test_two_language_mp4_mux_uses_mov_text_with_iso639_2_tags(tmp_path, ffmpeg_skill_install):
    _make_tiny_video(tmp_path / "in.mp4", duration=2.0)
    resp = execute(_mux_request(tmp_path, output="out.mp4"))
    streams = _subtitle_streams(Path(resp["output"]))
    assert [s["codec_name"] for s in streams] == ["mov_text", "mov_text"]
    assert [s["tags"].get("language") for s in streams] == ["jpn", "eng"]
    assert [t["stored_language"] for t in resp["tracks"]] == ["jpn", "eng"]
    assert [t["codec"] for t in resp["tracks"]] == ["mov_text", "mov_text"]


def test_translate_then_mux_pipeline(tmp_path, ffmpeg_skill_install):
    """The roadmap's 2-language integration test: a Japanese document is
    translated (engine 'memory', no model) and both languages are muxed."""
    _make_tiny_video(tmp_path / "in.mp4", duration=10.0)
    fx = json.loads((FIXTURES / "ja_en.json").read_text(encoding="utf-8"))
    translated = execute({
        "operation": "translate", "workspace": str(tmp_path), "format": "srt", "output_path": "ep01.en.srt",
        "engine": "memory", "target_language": "en", "glossary": fx["glossary"],
        "translation_memory": fx["translation_memory"], "subtitle": fx["source"],
    })
    resp = execute(_mux_request(tmp_path, tracks=[{"subtitle": fx["source"]}, {"subtitle": translated["subtitle"]}]))
    streams = _subtitle_streams(Path(resp["output"]))
    assert [s["tags"].get("language") for s in streams] == ["ja", "en"]
    en = _extract_srt(Path(resp["output"]), 1, tmp_path / "en.srt")
    assert "00:00:04,500 --> 00:00:09,000" in en
    assert "A word in the term list always\ngets the same translation." in en
    # The Japanese first track has lines wider than a 64 px frame. caption.py
    # re-wraps the first track for the frame and has no switch to stop it; the
    # timing must still pass through, and the re-wrap must be reported.
    ja = _extract_srt(Path(resp["output"]), 0, tmp_path / "ja.srt")
    assert "00:00:00,500 --> 00:00:03,000" in ja
    assert "00:00:04,500 --> 00:00:09,000" in ja
    relaid = [o for o in resp["observation"] if o["code"] == "TRACK_RELAID_OUT"]
    assert [o["cue_id"] for o in relaid] == ["ja"]
    again = execute(_mux_request(tmp_path, tracks=[{"subtitle": fx["source"]}, {"subtitle": translated["subtitle"]}]))
    assert again["reused"] is True
    assert [o["code"] for o in again["observation"]] == [o["code"] for o in resp["observation"]]


def test_mux_is_reused_on_repeat_and_redone_when_a_track_changes(tmp_path, ffmpeg_skill_install):
    _make_tiny_video(tmp_path / "in.mp4", duration=2.0)
    first = execute(_mux_request(tmp_path))
    second = execute(_mux_request(tmp_path))
    assert first["reused"] is False and second["reused"] is True
    assert second["tracks"] == first["tracks"]
    changed = _en_doc()
    changed["cues"][0]["text"] = "Yeah"
    third = execute(_mux_request(tmp_path, tracks=[{"subtitle": _ja_doc()}, {"subtitle": changed}]))
    assert third["reused"] is False


def test_cue_past_the_video_end_is_a_validation_error(tmp_path, ffmpeg_skill_install):
    _make_tiny_video(tmp_path / "in.mp4", duration=1.0)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_mux_request(tmp_path))
    assert exc.value.code == "VALIDATION_ERROR"
    assert any(d.get("cue_id") == "ja:2" for d in exc.value.details)


@pytest.mark.parametrize(
    "over,code",
    [
        ({"tracks": []}, "MISSING_INPUT"),
        ({"tracks": [{"subtitle": None}]}, "MISSING_INPUT"),
        ({"tracks": [{"subtitle": {"id": "x", "language": "en", "cues": [{"id": "1", "start": 0, "end": 1, "text": "a"}]}},
                     {"subtitle": {"id": "y", "language": "EN", "cues": [{"id": "1", "start": 0, "end": 1, "text": "b"}]}}]},
         "INVALID_INPUT"),
        ({"tracks": [{"subtitle": {"id": "x", "language": "english", "cues": [{"id": "1", "start": 0, "end": 1, "text": "a"}]}}]},
         "INVALID_INPUT"),
        ({"tracks": [{"subtitle": {"id": "x", "language": "ja", "cues": [{"id": "1", "start": 0, "end": 1, "text": "a"}]}, "title": "JA"},
                     {"subtitle": {"id": "y", "language": "en", "cues": [{"id": "1", "start": 0, "end": 1, "text": "b"}]}}]},
         "INVALID_INPUT"),
        ({"tracks": [{"subtitle": {"id": "x", "language": "en", "cues": [{"id": "1", "start": 0, "end": 1, "text": "a"}]}, "lang": "en"}]},
         "INVALID_INPUT"),
        ({"default_language": "fr"}, "INVALID_INPUT"),
        ({"format": "vtt"}, "UNSUPPORTED_FORMAT"),
        ({"output_path": "out.avi"}, "INVALID_INPUT"),
        ({"audio_stream": -1}, "INVALID_INPUT"),
        ({"mode": "burn"}, "INVALID_REQUEST"),
    ],
)
def test_invalid_mux_requests_fail_before_any_media_work(tmp_path, over, code, monkeypatch):
    # no ffmpeg-skill install and no video: every one of these must be refused first
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(tmp_path / "nowhere"))
    (tmp_path / "in.mp4").write_bytes(b"not a video")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_mux_request(tmp_path, **over))
    assert exc.value.code == code


def test_old_caption_without_multitrack_is_a_dependency_error(tmp_path, monkeypatch):
    """ffmpeg-skill before 1.16 takes one --srt: two tracks need the newer caption tool."""
    from subtitle_skill import engine

    root = tmp_path / "old-ffmpeg-skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "caption.py").write_text("# --mode mux, --language only\n", encoding="utf-8")
    (root / "scripts" / "_common.py").write_text("# fake\n", encoding="utf-8")
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    with pytest.raises(SubtitleSkillError) as exc:
        engine.mux_tracks(
            video_path=tmp_path / "in.mp4",
            tracks=[(tmp_path / "a.srt", "ja", None), (tmp_path / "b.srt", "en", None)],
            output_path=tmp_path / "out.mkv",
        )
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert "1.16" in exc.value.message
    assert engine.caption_supports_multitrack(VENDOR_ROOT) is True


def test_contract_advertises_mux_and_translate():
    from subtitle_skill.contract import build_contract

    contract = build_contract()
    assert set(contract["operations"]) == {"generate", "render", "translate", "mux"}
    mux = contract["operations"]["mux"]
    assert mux["delegates_to"] == {"skill_id": "ffmpeg-skill", "tool": "caption"}
    assert set(mux["containers"]) >= {".mp4", ".mkv"}
    assert contract["capabilities"]["translation_engines"] == ["argos", "deepl", "memory"]
    assert contract["operations"]["translate"]["default_engine"] == "argos"
    assert contract["parameters"]["mux"] == ["tracks", "default_language", "audio_stream"]
