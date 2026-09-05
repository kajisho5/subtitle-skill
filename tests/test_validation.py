import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.validation import SubtitleConstraints, validate_document


def _doc(cues):
    return SubtitleDocument.from_dict({"id": "d", "language": "en", "cues": cues})


def test_overlap_reported_not_fixed():
    doc = _doc(
        [
            {"id": "c1", "start": 0, "end": 3, "text": "a"},
            {"id": "c2", "start": 2, "end": 5, "text": "b"},
        ]
    )
    issues = validate_document(doc)
    codes = [i.code for i in issues]
    assert "CUE_OVERLAP" in codes
    assert len(doc.cues) == 2  # nothing was removed/moved


def test_empty_text_is_fatal():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "   "}])
    with pytest.raises(SubtitleSkillError) as exc:
        validate_document(doc)
    assert exc.value.code == "VALIDATION_ERROR"


def test_exceeds_video_duration_is_fatal():
    doc = _doc([{"id": "c1", "start": 0, "end": 10, "text": "a"}])
    with pytest.raises(SubtitleSkillError):
        validate_document(doc, video_duration=5)


def test_within_video_duration_ok():
    doc = _doc([{"id": "c1", "start": 0, "end": 4, "text": "a"}])
    validate_document(doc, video_duration=5)  # should not raise


def test_constraints_are_configurable_not_hardcoded():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "x" * 80}])
    loose = SubtitleConstraints.from_dict({"max_chars_per_line": 200})
    issues_loose = validate_document(doc, constraints=loose)
    assert not any(i.code == "LINE_TOO_LONG" for i in issues_loose)

    strict = SubtitleConstraints.from_dict({"max_chars_per_line": 5})
    issues_strict = validate_document(doc, constraints=strict)
    assert any(i.code == "LINE_TOO_LONG" for i in issues_strict)


def test_unknown_constraint_field_rejected():
    with pytest.raises(SubtitleSkillError):
        SubtitleConstraints.from_dict({"bogus": 1})
