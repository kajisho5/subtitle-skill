import math

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.models import SubtitleDocument


def _doc(**overrides):
    base = {
        "id": "doc1",
        "language": "en",
        "cues": [{"id": "c1", "start": 0.0, "end": 1.0, "text": "hi"}],
    }
    base.update(overrides)
    return base


def test_valid_document_roundtrip():
    doc = SubtitleDocument.from_dict(_doc())
    assert doc.id == "doc1"
    assert doc.cues[0].text == "hi"
    assert doc.to_dict()["cues"][0]["id"] == "c1"


@pytest.mark.parametrize(
    "cue,expected_code",
    [
        ({"id": "c1", "start": -1, "end": 2, "text": "x"}, "INVALID_TIME_RANGE"),
        ({"id": "c1", "start": 2, "end": 2, "text": "x"}, "INVALID_TIME_RANGE"),
        ({"id": "c1", "start": 2, "end": 1, "text": "x"}, "INVALID_TIME_RANGE"),
        ({"id": "c1", "start": math.nan, "end": 2, "text": "x"}, "INVALID_TIME_RANGE"),
        ({"id": "c1", "start": 0, "end": math.inf, "text": "x"}, "INVALID_TIME_RANGE"),
        ({"id": "", "start": 0, "end": 1, "text": "x"}, "INVALID_INPUT"),
        ({"id": "c1", "start": 0, "end": 1, "text": 5}, "INVALID_INPUT"),
    ],
)
def test_invalid_cue_rejected(cue, expected_code):
    with pytest.raises(SubtitleSkillError) as exc:
        SubtitleDocument.from_dict(_doc(cues=[cue]))
    assert exc.value.code == expected_code


def test_duplicate_cue_id_rejected():
    cues = [
        {"id": "c1", "start": 0, "end": 1, "text": "a"},
        {"id": "c1", "start": 2, "end": 3, "text": "b"},
    ]
    with pytest.raises(SubtitleSkillError) as exc:
        SubtitleDocument.from_dict(_doc(cues=cues))
    assert exc.value.code == "VALIDATION_ERROR"


def test_invalid_language_rejected():
    with pytest.raises(SubtitleSkillError) as exc:
        SubtitleDocument.from_dict(_doc(language="!!!"))
    assert exc.value.code == "INVALID_INPUT"


def test_unknown_field_rejected():
    with pytest.raises(SubtitleSkillError) as exc:
        SubtitleDocument.from_dict(_doc(unexpected="field"))
    assert exc.value.code == "INVALID_INPUT"


def test_unknown_style_field_rejected():
    cues = [{"id": "c1", "start": 0, "end": 1, "text": "a", "style": {"filter": "blur"}}]
    with pytest.raises(SubtitleSkillError) as exc:
        SubtitleDocument.from_dict(_doc(cues=cues))
    assert exc.value.code == "INVALID_INPUT"
