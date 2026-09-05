import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.operations import execute


def _base_request(**overrides):
    req = {
        "operation": "generate",
        "workspace": None,  # filled by caller
        "format": "srt",
        "output_path": "out.srt",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hi"}],
        },
    }
    req.update(overrides)
    return req


@pytest.mark.parametrize(
    "forbidden_key",
    ["command", "argv", "shell", "executable", "filter", "filter_complex", "vf", "af", "env", "api_key"],
)
def test_forbidden_keys_rejected_top_level(tmp_path, forbidden_key):
    req = _base_request(workspace=str(tmp_path))
    req[forbidden_key] = "anything"
    with pytest.raises(SubtitleSkillError) as exc:
        execute(req)
    assert exc.value.code == "INVALID_REQUEST"


def test_forbidden_keys_rejected_nested(tmp_path):
    req = _base_request(workspace=str(tmp_path))
    req["subtitle"]["metadata"] = {"filter_complex": "evil"}
    with pytest.raises(SubtitleSkillError) as exc:
        execute(req)
    assert exc.value.code == "INVALID_REQUEST"


def test_path_traversal_in_output_rejected(tmp_path):
    req = _base_request(workspace=str(tmp_path), output_path="../escape.srt")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(req)
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_absolute_output_path_rejected(tmp_path):
    req = _base_request(workspace=str(tmp_path), output_path="/tmp/evil.srt")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(req)
    assert exc.value.code == "PATH_NOT_ALLOWED"
