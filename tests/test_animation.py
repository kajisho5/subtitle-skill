"""Roadmap step 12: animated captions from cues (docs/adr/ADR-011-animated-captions.md).

`render` passes `animate` / `karaoke` through to ffmpeg-skill `caption --animate` /
`--karaoke --karaoke-style`; the burns below go through the vendored caption.py and
real ffmpeg on a short lavfi clip, and the decoded frames are read back.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.operations import execute

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "animation" / "talk_words_en.json").read_text(encoding="utf-8"))
# SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC points these tests at a real ffmpeg-skill checkout
# instead of the pinned copy (.github/workflows/vendor-drift.yml, against its current main)
VENDOR_ROOT = Path(os.environ.get("SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC") or FIXTURES / "ffmpeg_skill_vendor")
W, H, FPS = 320, 180, 25
CUE_START, CUE_END = 1.0, 2.6


@pytest.fixture
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _video(path, duration=3):
    if not shutil.which("ffmpeg"):
        if os.environ.get("SUBTITLE_SKILL_REQUIRE_REAL_ENGINE") == "1":
            pytest.fail("SUBTITLE_SKILL_REQUIRE_REAL_ENGINE=1 but ffmpeg is not on PATH")
        pytest.skip("real ffmpeg is required for render tests")
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d={duration}:r={FPS}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _request(tmp_path, subtitle=FIXTURE, **extra):
    return {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
            "video_input": "in.mp4", "subtitle": subtitle, **extra}


class Frames:
    """Every decoded frame of a burned clip, as RGB bytes, indexed by time."""

    def __init__(self, path):
        data = subprocess.run(
            [shutil.which("ffmpeg"), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        size = W * H * 3
        self.frames = [data[i:i + size] for i in range(0, len(data), size)]

    def at(self, t):
        return self.frames[round(t * FPS)]

    def ink(self, t):
        """How much caption is drawn: summed brightness above the black background."""
        frame = self.at(t)
        return sum(max(0, max(frame[i], frame[i + 1], frame[i + 2]) - 24) for i in range(0, len(frame), 3))

    def centre_y(self, t):
        frame = self.at(t)
        ys = [(i // 3) // W for i in range(0, len(frame), 3) if max(frame[i], frame[i + 1], frame[i + 2]) > 100]
        return sum(ys) / len(ys) if ys else None

    def colours(self, t):
        """(highlight, white) pixel counts: caption.py's karaoke highlight is FFD200."""
        frame = self.at(t)
        yellow = white = 0
        for i in range(0, len(frame), 3):
            r, g, b = frame[i], frame[i + 1], frame[i + 2]
            if r > 200 and g > 150 and b < 110:
                yellow += 1
            elif r > 215 and g > 215 and b > 215:
                white += 1
        return yellow, white


# --- animate: absent / partial / full at the expected frames of the cue ---------------------

# the frame inside each entrance (caption.py's ASS: fade \fad(200,200); pop \fad(80,120) with a
# scale from 60 % over 120 ms; slide \fad(150,150) with a \move over 250 ms)
PARTIAL_AT = {"fade": 1.08, "pop": 1.04, "slide": 1.08}


@pytest.mark.parametrize("mode", ["fade", "pop", "slide"])
def test_each_animate_mode_burns_absent_partial_full(tmp_path, ffmpeg_skill_install, mode):
    _video(tmp_path / "in.mp4")
    response = execute(_request(tmp_path, animate=mode))
    assert response["animation"] == {"animate": mode, "karaoke": None, "word_timings": None,
                                     "engine_flags": ["--animate", mode]}
    frames = Frames(tmp_path / "out.mp4")
    full = frames.ink(2.0)
    assert full > 0
    assert frames.ink(CUE_START - 0.04) == 0                # before the cue
    assert frames.ink(CUE_START) < 0.01 * full             # absent on the cue's first frame
    assert 0.05 * full < frames.ink(PARTIAL_AT[mode]) < 0.6 * full   # partial during the entrance
    assert abs(frames.ink(1.3) - full) < 0.05 * full       # full once it is over
    assert frames.ink(CUE_END + 0.04) == 0                 # gone after the cue
    if mode == "slide":
        # slides up into place from below
        assert frames.centre_y(PARTIAL_AT[mode]) > frames.centre_y(1.3) + 3


def test_a_static_burn_is_full_on_its_first_frame(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    response = execute(_request(tmp_path))
    assert "animation" not in response
    frames = Frames(tmp_path / "out.mp4")
    assert frames.ink(CUE_START) == frames.ink(2.0) > 0


# --- karaoke -------------------------------------------------------------------------------


def test_karaoke_sweep_follows_the_cue_words(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    response = execute(_request(tmp_path, karaoke="sweep"))
    assert response["animation"]["karaoke"] == "sweep"
    assert response["animation"]["word_timings"] == "cue.words"
    assert response["animation"]["engine_flags"] == [
        "--karaoke", "--karaoke-style", "sweep", "--karaoke-timing", "even"]
    frames = Frames(tmp_path / "out.mp4")
    start_yellow, start_white = frames.colours(CUE_START)
    assert start_yellow == 0 and start_white > 0           # absent: nothing highlighted yet
    mid_yellow, mid_white = frames.colours(1.3)
    assert mid_yellow > 0 and mid_white > 0                # partial
    end_yellow, end_white = frames.colours(2.56)
    assert end_yellow > mid_yellow and end_white <= 2      # full by the cue's end
    # MOVE and WITH are spoken by 1.4 s (cue.words); an even split would still be inside MOVE
    _yellow, white = frames.colours(1.45)
    assert white < 0.4 * start_white


def test_karaoke_word_highlights_the_spoken_word(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    execute(_request(tmp_path, karaoke="word"))
    frames = Frames(tmp_path / "out.mp4")
    yellow, white = frames.colours(1.12)
    assert yellow > 0 and white == 0                       # MOVE active, nothing spoken yet
    yellow, white = frames.colours(1.45)
    # ME is active from 1.4 s (cue.words): MOVE and WITH are past, drawn white
    assert yellow > 0 and white > 0


def test_karaoke_sweep_with_an_entrance_stays_on_screen_for_the_whole_cue(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    execute(_request(tmp_path, karaoke="sweep", animate="pop"))
    frames = Frames(tmp_path / "out.mp4")
    assert frames.ink(CUE_START) == 0
    inks = [frames.ink(CUE_START + n / FPS) for n in range(8, int((CUE_END - CUE_START) * FPS) - 4)]
    assert min(inks) > 0.8 * max(inks)                     # popped in once, never blinks out mid-cue


def test_karaoke_word_with_an_entrance_is_refused(tmp_path, ffmpeg_skill_install):
    """ffmpeg-skill caption gives the entrance's \\fad to the first per-word event only, so the
    caption fades out at the end of the first word (measured at 2.24.0 and 3.8.3)."""
    _video(tmp_path / "in.mp4")
    for mode in ("fade", "pop", "slide"):
        with pytest.raises(SubtitleSkillError) as exc:
            execute(_request(tmp_path, karaoke="word", animate=mode))
        assert exc.value.code == "INVALID_INPUT"
        assert exc.value.details == {"field": "animate", "conflicts_with": ["karaoke"]}


def test_karaoke_words_survive_the_engines_own_cue_adjustment(tmp_path, ffmpeg_skill_install):
    """A cue shorter than caption.py's --min-duration (1 s) is lengthened into an
    `<output>_adjusted.srt`; the word timings must still be the cue's own."""
    _video(tmp_path / "in.mp4")
    short = {"id": "short", "language": "en", "cues": [{
        "id": "1", "start": 1.0, "end": 1.5, "text": "MOVE WITH ME",
        "words": [{"text": "MOVE", "start": 1.0, "end": 1.1}, {"text": "WITH", "start": 1.1, "end": 1.2},
                  {"text": "ME", "start": 1.2, "end": 1.5}]}]}
    execute(_request(tmp_path, subtitle=short, karaoke="word"))
    assert (tmp_path / "out_adjusted.srt").exists()        # the engine did adjust the cue
    frames = Frames(tmp_path / "out.mp4")
    # ME is active from 1.2 s; an even split of the lengthened 1.0-2.0 s cue would still be on MOVE
    _yellow, white = frames.colours(1.28)
    assert white > 0


def test_no_side_files_are_left_in_the_workspace(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    execute(_request(tmp_path, karaoke="sweep", animate="fade"))
    left = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert not [n for n in left if n.endswith((".ass", ".words.json"))], left
    assert "out.mp4" in left


# --- refusals ------------------------------------------------------------------------------


def _without_words(data=FIXTURE):
    return {**data, "cues": [{k: v for k, v in c.items() if k != "words"} for c in data["cues"]]}


def test_karaoke_without_words_is_refused_naming_the_field(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, subtitle=_without_words(), karaoke="sweep"))
    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details["field"] == "subtitle.cues[0].words"
    assert not (tmp_path / "out.mp4").exists()


def test_the_missing_words_are_named_by_their_index_in_the_request(tmp_path, ffmpeg_skill_install):
    later = {**FIXTURE["cues"][0], "id": "2", "start": 2.7, "end": 2.9, "text": "GO",
             "words": [{"text": "GO", "start": 2.7, "end": 2.9}]}
    earlier = {k: v for k, v in FIXTURE["cues"][0].items() if k != "words"}
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, subtitle={**FIXTURE, "cues": [later, earlier]}, karaoke="sweep"))
    assert exc.value.details["field"] == "subtitle.cues[1].words"


@pytest.mark.parametrize("name", ["out_adjusted.words.json", "out.subtitle-skill-src.words.json",
                                  "out.subtitle-skill-src.animated.ass"])
def test_an_existing_side_file_is_refused_and_kept(tmp_path, ffmpeg_skill_install, name):
    _video(tmp_path / "in.mp4")
    (tmp_path / name).write_text("theirs", encoding="utf-8")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, karaoke="sweep"))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == "output_path"
    assert (tmp_path / name).read_text(encoding="utf-8") == "theirs"
    assert not (tmp_path / "out.mp4").exists()


def test_karaoke_words_that_do_not_match_the_drawn_text_are_refused(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    speaker = {**FIXTURE, "cues": [{**FIXTURE["cues"][0], "speaker": "Host"}]}  # "Host:" is drawn too
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, subtitle=speaker, karaoke="word"))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == "subtitle.cues[0].words"


@pytest.mark.parametrize("extra", [{"animate": "pop"}, {"karaoke": "sweep"}])
def test_an_animation_on_a_soft_track_is_refused(tmp_path, ffmpeg_skill_install, extra):
    _video(tmp_path / "in.mp4")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, mode="mux", output_path="out.mkv", **extra))
    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details == {"field": next(iter(extra)), "conflicts_with": ["mode"]}


def test_an_animation_through_the_skills_own_ass_is_refused_with_the_reason(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    ruby = {**FIXTURE, "cues": [{**FIXTURE["cues"][0], "ruby": [{"base": "ME", "text": "mi"}]}]}
    cases = [
        (_request(tmp_path, animate="fade", placement={"avoid": [{"name": "lower-third", "box": [0.0, 0.7, 1.0, 0.3]}]}),
         "placement"),
        (_request(tmp_path, subtitle=ruby, animate="slide"), "ruby"),
        (_request(tmp_path, animate="pop", caption_profile="shorts"), "caption_profile"),
    ]
    for request, through in cases:
        with pytest.raises(SubtitleSkillError) as exc:
            execute(request)
        assert exc.value.code == "INVALID_INPUT", through
        assert exc.value.details["field"] == "animate"
        assert through in exc.value.details["conflicts_with"]
        assert "ADR-011" in exc.value.message


@pytest.mark.parametrize("extra, field", [({"animate": "spin"}, "animate"), ({"karaoke": True}, "karaoke"),
                                          ({"karaoke": "bounce"}, "karaoke")])
def test_unknown_values_are_refused(tmp_path, extra, field):
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, **extra))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == field


# --- a burn with no animation sends the call it sent before ----------------------------------


def test_no_animation_sends_the_same_engine_call(tmp_path, monkeypatch):
    """`animate` absent, `animate: "none"`, and the burn_in call before step 12 build one argv."""
    from test_engine_style import _argv_after_video, _base_argv_tail, _burn_in, _write_fake_scripts

    root = tmp_path / "ffskill"
    _write_fake_scripts(root)
    (root / "scripts" / "caption.py").write_text(
        (root / "scripts" / "caption.py").read_text(encoding="utf-8")
        + "\n# " + " ".join(['"--animate"', '"--karaoke"', '"--karaoke-style"', '"--karaoke-timing"', '"--write-ass"'])
        + "\n", encoding="utf-8")
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    before = _argv_after_video(_burn_in(tmp_path), tmp_path)
    assert before == _base_argv_tail(tmp_path)
    assert _argv_after_video(_burn_in(tmp_path, animate=None, karaoke=None), tmp_path) == before
    assert _argv_after_video(_burn_in(tmp_path, animate="none"), tmp_path) == before
    moved = _argv_after_video(_burn_in(tmp_path, animate="pop"), tmp_path)
    assert moved[:len(before)] == before and moved[len(before):len(before) + 2] == ["--animate", "pop"]


def test_animate_none_is_the_same_render_as_no_animate(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    first = execute(_request(tmp_path))
    again = execute(_request(tmp_path, animate="none"))
    assert again["reused"] is True and again["sha256"] == first["sha256"] and "animation" not in again


def test_an_animated_render_is_not_reused_for_a_static_one(tmp_path, ffmpeg_skill_install):
    _video(tmp_path / "in.mp4")
    static = execute(_request(tmp_path))
    moving = execute(_request(tmp_path, animate="fade"))
    assert moving["reused"] is False and moving["sha256"] != static["sha256"]
    again = execute(_request(tmp_path, animate="fade"))
    assert again["reused"] is True and again["animation"] == moving["animation"]


def test_an_engine_without_the_flags_is_a_dependency_error(tmp_path, monkeypatch):
    from test_engine_style import _burn_in, _write_fake_scripts

    root = tmp_path / "ffskill"
    _write_fake_scripts(root)  # a caption.py with none of the animation flags
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    with pytest.raises(SubtitleSkillError) as exc:
        _burn_in(tmp_path, animate="fade")
    assert exc.value.code == "DEPENDENCY_ERROR"
