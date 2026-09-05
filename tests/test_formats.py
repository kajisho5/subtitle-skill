import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats import generate_srt, generate_vtt
from subtitle_skill.formats.srt import _format_timestamp as _srt_ts
from subtitle_skill.formats.vtt import _format_timestamp as _vtt_ts
from subtitle_skill.models import SubtitleDocument


@pytest.mark.parametrize(
    "seconds,expected_srt,expected_vtt",
    [
        (0, "00:00:00,000", "00:00:00.000"),
        (0.001, "00:00:00,001", "00:00:00.001"),
        (0.999, "00:00:00,999", "00:00:00.999"),
        (1.000, "00:00:01,000", "00:00:01.000"),
        (59.999, "00:00:59,999", "00:00:59.999"),
        (60.000, "00:01:00,000", "00:01:00.000"),
        (3599.999, "00:59:59,999", "00:59:59.999"),
        (3600.000, "01:00:00,000", "01:00:00.000"),
    ],
)
def test_timestamp_rounding_boundaries(seconds, expected_srt, expected_vtt):
    assert _srt_ts(seconds) == expected_srt
    assert _vtt_ts(seconds) == expected_vtt


def _doc(cues, **kw):
    d = {"id": "d", "language": "en", "cues": cues}
    d.update(kw)
    return SubtitleDocument.from_dict(d)


def test_srt_basic():
    doc = _doc([{"id": "c1", "start": 0, "end": 1.5, "text": "hello"}])
    out = generate_srt(doc)
    assert "00:00:00,000 --> 00:00:01,500" in out
    assert "hello" in out
    assert out.count("1\n") >= 1


def test_srt_speaker_prefix_and_multiline():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "line1\nline2", "speaker": "Alice"}])
    out = generate_srt(doc)
    assert "Alice: line1" in out
    assert "line2" in out


def test_srt_unicode():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "こんにちは世界"}])
    out = generate_srt(doc)
    assert "こんにちは世界" in out


def test_srt_rejects_unsupported_style():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "x", "style": {"position": 50}}])
    with pytest.raises(SubtitleSkillError) as exc:
        generate_srt(doc)
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_vtt_basic_header_and_timestamp():
    doc = _doc([{"id": "c1", "start": 0, "end": 1.234, "text": "hi"}])
    out = generate_vtt(doc)
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.234" in out


def test_vtt_cue_settings():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "hi", "style": {"align": "center", "position": 50}}])
    out = generate_vtt(doc)
    assert "align:center" in out
    assert "position:50%" in out


def test_vtt_escapes_html_like_chars():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "a < b & c > d"}])
    out = generate_vtt(doc)
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out


def test_vtt_rejects_color_style():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "x", "style": {"color": "red"}}])
    with pytest.raises(SubtitleSkillError) as exc:
        generate_vtt(doc)
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_output_ordered_by_start_regardless_of_input_order():
    doc = _doc(
        [
            {"id": "c2", "start": 5, "end": 6, "text": "LATER_TEXT"},
            {"id": "c1", "start": 0, "end": 1, "text": "EARLIER_TEXT"},
        ]
    )
    out = generate_srt(doc)
    assert out.index("EARLIER_TEXT") < out.index("LATER_TEXT")
