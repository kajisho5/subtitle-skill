import os

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.pathpolicy import PathPolicy


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "ws"


def test_resolve_output_creates_within_root(workspace):
    policy = PathPolicy(workspace)
    out = policy.resolve_output("subdir/out.srt")
    assert str(out).startswith(str(policy.root))


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.srt",
        "a/../../escape.srt",
        "/etc/passwd",
        "C:\\Windows\\evil.srt",
        "sub/../../../etc/passwd",
    ],
)
def test_traversal_rejected(workspace, bad_path):
    policy = PathPolicy(workspace)
    with pytest.raises(SubtitleSkillError) as exc:
        policy.resolve_output(bad_path)
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_symlink_escape_rejected(workspace, tmp_path):
    policy = PathPolicy(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "escape_link"
    os.symlink(outside, link)
    with pytest.raises(SubtitleSkillError) as exc:
        policy.resolve_output("escape_link/out.srt")
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_missing_input_rejected(workspace):
    policy = PathPolicy(workspace)
    with pytest.raises(SubtitleSkillError) as exc:
        policy.resolve_input("does_not_exist.mp4")
    assert exc.value.code == "MISSING_INPUT"


def test_reserved_windows_name_rejected(workspace):
    policy = PathPolicy(workspace)
    with pytest.raises(SubtitleSkillError):
        policy.resolve_output("CON.srt")
