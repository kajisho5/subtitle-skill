"""Unit tests for engine._style_argv and engine.burn_in's style wiring
(kajisho5/subtitle-skill#5): the argv translation of the SubtitleStyle
fields that DO map onto caption.py's real flags (color/bold/size), and
confirmation that the fields which do NOT (align/position/line/italic)
never reach argv at all.

These test subtitle_skill's own arithmetic and argv construction with a
minimal FAKE caption/probe pair (see tests/test_engine_boundaries.py for
the same pattern) -- the real, vendored caption.py is exercised separately
in tests/test_engine_render.py.
"""
import math
import textwrap
from pathlib import Path

import pytest


def _write_fake_scripts(root: Path) -> None:
    """A fake caption.py that records the *exact* argv it received (besides
    the trailing --json this module always appends) so tests can assert on
    it precisely, and a fake probe.py that reports a plain 10s video.
    """
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "_common.py").write_text("# fake\n", encoding="utf-8")

    probe_src = textwrap.dedent(
        """
        import json, sys
        def main():
            path = sys.argv[1]
            print(json.dumps({"file": path, "duration": 10.0, "video": {"codec": "h264"}, "audio": None}))
        if __name__ == "__main__":
            main()
        """
    )
    (root / "scripts" / "probe.py").write_text(probe_src, encoding="utf-8")

    caption_src = textwrap.dedent(
        """
        import json, sys
        def main():
            args = sys.argv[1:]
            recorded = [a for a in args if a != "--json"]
            out_index = args.index("-o") + 1
            output_path = args[out_index]
            with open(output_path, "wb") as f:
                f.write(b"fake mp4 bytes")
            probe = {"file": output_path, "duration": 10.0, "video": {"codec": "h264"}, "audio": None}
            print(json.dumps({
                "status": "completed", "output": output_path, "dry_run": False,
                "commands": ["fake ffmpeg cmd"], "probe": probe, "recorded_argv": recorded,
            }))
        if __name__ == "__main__":
            main()
        """
    )
    (root / "scripts" / "caption.py").write_text(caption_src, encoding="utf-8")


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    root = tmp_path / "ffskill"
    _write_fake_scripts(root)
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    return root


def _burn_in(tmp_path, **kwargs):
    import subtitle_skill.engine as engine

    video_path = tmp_path / "in.mp4"
    video_path.write_bytes(b"fake input video")
    srt_path = tmp_path / "cues.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n", encoding="utf-8")
    output_path = tmp_path / "out.mp4"
    return engine.burn_in(
        video_path=video_path, subtitle_path=srt_path, subtitle_format="srt", output_path=output_path, **kwargs
    )


def _base_argv_tail(tmp_path):
    return ["--srt", str(tmp_path / "cues.srt"), "-o", str(tmp_path / "out.mp4")]


def _argv_after_video(response, tmp_path):
    argv = response["recorded_argv"]
    assert argv[0] == str(tmp_path / "in.mp4")
    return argv[1:]


# --- pure _style_argv unit tests -------------------------------------------------


def test_style_argv_all_unset_style_is_a_no_op():
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.models import SubtitleStyle

    assert _style_argv(SubtitleStyle()) == []


@pytest.mark.parametrize(
    "unmapped_field,value",
    [("align", "center"), ("position", 50.0), ("line", 10.0), ("italic", True)],
)
def test_style_argv_fields_with_no_caption_equivalent_add_nothing(unmapped_field, value):
    """align/position/line/italic have no caption.py equivalent (see
    engine._style_argv's docstring) -- setting only one of them must not
    add any argv at all, not a guessed-at flag."""
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.models import SubtitleStyle

    style = SubtitleStyle(**{unmapped_field: value})
    assert _style_argv(style) == []


def test_style_argv_color_forwarded_verbatim():
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.models import SubtitleStyle

    assert _style_argv(SubtitleStyle(color="00ff00")) == ["--color", "00ff00"]
    assert _style_argv(SubtitleStyle(color="#ABCDEF")) == ["--color", "#ABCDEF"]


def test_style_argv_color_wrong_type_is_invalid_input():
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.errors import SubtitleSkillError
    from subtitle_skill.models import SubtitleStyle

    with pytest.raises(SubtitleSkillError) as exc:
        _style_argv(SubtitleStyle(color=123))
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("bold,expected", [(True, ["--bold"]), (False, []), (None, [])])
def test_style_argv_bold(bold, expected):
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.models import SubtitleStyle

    assert _style_argv(SubtitleStyle(bold=bold)) == expected


def test_style_argv_bold_wrong_type_is_invalid_input():
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.errors import SubtitleSkillError
    from subtitle_skill.models import SubtitleStyle

    with pytest.raises(SubtitleSkillError) as exc:
        _style_argv(SubtitleStyle(bold="yes"))
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "size_percent,expected_points",
    [
        (100, 288),  # percent of the full 288-line baseline -> the whole baseline
        (50, 144),
        (8.333333, 24),  # caption.py's own default size (24) expressed as this percent
        (0.5, round(0.5 / 100 * 288)),
    ],
)
def test_style_argv_size_percent_of_288_conversion(size_percent, expected_points):
    from subtitle_skill.engine import _style_argv, _ASS_SCRIPT_HEIGHT
    from subtitle_skill.models import SubtitleStyle

    assert _ASS_SCRIPT_HEIGHT == 288
    assert _style_argv(SubtitleStyle(size=size_percent)) == ["--size", str(expected_points)]


@pytest.mark.parametrize("bad_size", [0, -5, math.nan, math.inf, True])
def test_style_argv_size_invalid_values_rejected(bad_size):
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.errors import SubtitleSkillError
    from subtitle_skill.models import SubtitleStyle

    with pytest.raises(SubtitleSkillError) as exc:
        _style_argv(SubtitleStyle(size=bad_size))
    assert exc.value.code == "INVALID_INPUT"


def test_style_argv_combines_all_three_wired_fields():
    from subtitle_skill.engine import _style_argv
    from subtitle_skill.models import SubtitleStyle

    argv = _style_argv(SubtitleStyle(color="FF00FF", bold=True, size=25))
    assert argv == ["--color", "FF00FF", "--bold", "--size", str(round(25 / 100 * 288))]


# --- engine.burn_in argv-construction (fake caption.py, full argv capture) -------


def test_burn_in_unset_style_produces_identical_argv_to_before_the_change(tmp_path, fake_engine):
    """The regression guard the task explicitly calls for: an all-default
    (or omitted) style must not add any new flags at all."""
    response_no_kwarg = _burn_in(tmp_path)
    response_explicit_none = _burn_in(tmp_path, style=None)

    argv_no_kwarg = _argv_after_video(response_no_kwarg, tmp_path)
    argv_explicit_none = _argv_after_video(response_explicit_none, tmp_path)

    assert argv_no_kwarg == argv_explicit_none == _base_argv_tail(tmp_path)
    assert not any(a.startswith("--color") or a.startswith("--bold") or a.startswith("--size") for a in argv_no_kwarg)


def test_burn_in_forwards_color_bold_size_for_mode_burn(tmp_path, fake_engine):
    from subtitle_skill.models import SubtitleStyle

    response = _burn_in(tmp_path, style=SubtitleStyle(color="112233", bold=True, size=50))
    argv = _argv_after_video(response, tmp_path)
    assert argv == _base_argv_tail(tmp_path) + ["--color", "112233", "--bold", "--size", "144"]


def test_burn_in_does_not_forward_style_for_mode_mux(tmp_path, fake_engine):
    """caption.py's own docs: styling has no meaning for a soft-muxed
    stream -- mode="mux" must silently not forward it, not error."""
    from subtitle_skill.models import SubtitleStyle

    response = _burn_in(tmp_path, mode="mux", style=SubtitleStyle(color="112233", bold=True, size=50))
    argv = _argv_after_video(response, tmp_path)
    assert "--color" not in argv and "--bold" not in argv and "--size" not in argv
    assert "--mode" in argv and "mux" in argv


def test_burn_in_rejects_wrong_type_for_style(tmp_path, fake_engine):
    from subtitle_skill.errors import SubtitleSkillError

    with pytest.raises(SubtitleSkillError) as exc:
        _burn_in(tmp_path, style={"color": "112233"})
    assert exc.value.code == "INVALID_INPUT"
