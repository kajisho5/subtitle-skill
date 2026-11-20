"""Caption quality for talking-head video (docs/adr/ADR-013-caption-quality.md): cut-aware
cues, spoken numbers as one token, emphasis, automatic legibility.

The burns go through the vendored ffmpeg-skill caption.py and real ffmpeg on synthetic lavfi
clips (a hard cut from a dark to a bright picture mid-cue, plain dark and bright pictures),
and the decoded frames are read back.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.cuts import apply_cuts, read_shots
from subtitle_skill.emphasis import parse_emphasis, plan_emphasis
from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats.ass import Look, generate_ass
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.numbers import NBSP, normalize_document, rewrite_text
from subtitle_skill.operations import execute

FIXTURES = Path(__file__).parent / "fixtures"
VENDOR_ROOT = Path(os.environ.get("SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC") or FIXTURES / "ffmpeg_skill_vendor")
W, H, FPS = 320, 180, 25
CUT = 1.52  # the first frame of the second shot (frame 38 at 25 fps)
DARK, BRIGHT = "0x101010", "0xF0F0F0"


def _doc(*cues, language="en"):
    return SubtitleDocument.from_dict({"id": "d", "language": language, "cues": list(cues)})


# --- spoken numbers -------------------------------------------------------------------------

@pytest.mark.parametrize("spoken,written", [
    ("We raised five thousand dollars.", "We raised $5,000."),
    ("Forty percent of users", "40% of users"),
    ("about 40 per cent", "about 40%"),
    ("It costs two point five million dollars!", f"It costs $2.5{NBSP}million!"),
    ("a million dollars", f"$1{NBSP}million"),
    ("5,000 dollars", "$5,000"),
    ("forty euros", "€40"),
    ("in nineteen ninety-nine we", "in 1999 we"),
    ("twenty twenty-four was big", "2024 was big"),
    ("since two thousand and eight", "since 2008"),
    ("twenty oh five", "2005"),
    ("May fifth", f"May{NBSP}5"),
    ("on March 3rd, twenty twenty-four we", f"on March{NBSP}3,{NBSP}2024 we"),
    ("twenty-five people came", "25 people came"),
    ("one hundred and five", "105"),
    ("twelve hundred", "1,200"),
    ("five million people", f"5{NBSP}million people"),
    ("three point five", "3.5"),
])
def test_spoken_number_phrases_are_written_as_one_token(spoken, written):
    assert rewrite_text(spoken)[0] == written


@pytest.mark.parametrize("text", ["I have two kids", "you may go", "one two three", "the first time", "zero"])
def test_text_that_is_not_a_number_phrase_is_left_as_it_is(text):
    assert rewrite_text(text) == (text, [])


def test_number_words_are_merged_into_one_timed_word():
    doc = _doc({"id": "c1", "start": 0.0, "end": 2.0, "text": "five thousand dollars raised", "words": [
        {"text": "five", "start": 0.0, "end": 0.3}, {"text": "thousand", "start": 0.3, "end": 0.7},
        {"text": "dollars", "start": 0.7, "end": 1.1}, {"text": "raised", "start": 1.2, "end": 1.6}]})
    out, report = normalize_document(doc, "en")
    cue = out.cues[0]
    assert cue.text == "$5,000 raised"
    assert [(w.text, w.start, w.end) for w in cue.words] == [("$5,000", 0.0, 1.1), ("raised", 1.2, 1.6)]
    assert report["replacements"] == [{"cue_id": "c1", "spoken": "five thousand dollars", "written": "$5,000"}]


def test_a_cue_with_ruby_is_not_rewritten():
    doc = _doc({"id": "c1", "start": 0, "end": 2, "text": "twenty five", "ruby": [{"base": "twenty", "text": "x"}]})
    out, report = normalize_document(doc, "en")
    assert out.cues[0].text == "twenty five" and report["replacements"] == []


def _generate(tmp_path, **extra):
    request = {"operation": "generate", "workspace": str(tmp_path), "format": "srt", "output_path": "out.srt",
               "subtitle": {"id": "d", "language": "en", "cues": [
                   {"id": "c1", "start": 0.0, "end": 2.0, "text": "We raised five thousand dollars"}]}, **extra}
    return execute(request)


def test_generate_writes_spoken_numbers_only_when_asked(tmp_path):
    plain = _generate(tmp_path)
    assert "spoken_numbers" not in plain
    assert "five thousand dollars" in (tmp_path / "out.srt").read_text(encoding="utf-8")
    written = _generate(tmp_path, spoken_numbers="en")
    assert written["spoken_numbers"]["replacements"][0]["written"] == "$5,000"
    assert "We raised $5,000" in (tmp_path / "out.srt").read_text(encoding="utf-8")
    again = _generate(tmp_path)
    assert again["sha256"] == plain["sha256"]


def test_an_unknown_numbers_locale_is_refused(tmp_path):
    with pytest.raises(SubtitleSkillError) as exc:
        _generate(tmp_path, spoken_numbers="xx")
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == "spoken_numbers"


# --- cut-aware cues: the rule -----------------------------------------------------------------

def test_a_cue_across_a_cut_is_split_at_it_with_its_words():
    doc = _doc({"id": "c1", "start": 0.0, "end": 3.0, "text": "one two three four", "words": [
        {"text": "one", "start": 0.0, "end": 0.5}, {"text": "two", "start": 0.6, "end": 1.2},
        {"text": "three", "start": 1.6, "end": 2.2}, {"text": "four", "start": 2.3, "end": 2.9}]})
    out, report = apply_cuts(doc, [1.4], min_duration=0.5)
    assert [(c.id, c.start, c.end, c.text) for c in out.cues] == [
        ("c1.a", 0.0, 1.4, "one two"), ("c1.b", 1.4, 3.0, "three four")]
    assert [w.text for w in out.cues[1].words] == ["three", "four"]
    assert report["changes"][0]["action"] == "split" and report["unresolved"] == []


def test_a_short_head_moves_the_start_and_a_short_tail_moves_the_end():
    doc = _doc({"id": "a", "start": 0.0, "end": 2.0, "text": "hello there"},
               {"id": "b", "start": 3.0, "end": 5.0, "text": "see you"})
    out, report = apply_cuts(doc, [0.3, 4.8], min_duration=0.5)
    assert [(c.id, c.start, c.end) for c in out.cues] == [("a", 0.3, 2.0), ("b", 3.0, 4.8)]
    assert [c["action"] for c in report["changes"]] == ["start_moved", "end_moved"]


def test_a_cue_too_short_for_any_move_is_reported_and_left():
    doc = _doc({"id": "a", "start": 0.0, "end": 0.8, "text": "hi there"})
    out, report = apply_cuts(doc, [0.4], min_duration=0.5)
    assert out.cues[0].start == 0.0 and out.cues[0].end == 0.8
    assert report["unresolved"][0]["cue_id"] == "a"


def test_a_cut_at_a_cue_boundary_changes_nothing():
    doc = _doc({"id": "a", "start": 0.0, "end": 1.0, "text": "x y"}, {"id": "b", "start": 1.0, "end": 2.0, "text": "z w"})
    out, report = apply_cuts(doc, [1.0, 1.02], min_duration=0.5)
    assert out == doc and report["changes"] == []


def test_a_written_number_is_never_split_by_a_cut():
    doc = _doc({"id": "a", "start": 0.0, "end": 2.0, "text": f"we raised $2.5{NBSP}million today"})
    out, _ = apply_cuts(doc, [1.0], min_duration=0.5)
    assert any(f"$2.5{NBSP}million" in c.text for c in out.cues)
    assert all(c.text.split(" ") for c in out.cues)


def test_every_remaining_cue_respects_the_minimum_duration():
    doc = _doc({"id": "a", "start": 0.0, "end": 4.0, "text": "a b c d e f g h"})
    out, _ = apply_cuts(doc, [0.7, 1.1, 2.0, 3.6], min_duration=0.5)
    starts = [c.start for c in out.cues]
    assert starts == sorted(starts)
    for cue in out.cues:
        for cut in (0.7, 1.1, 2.0, 3.6):
            assert not (cue.start + 0.04 < cut < cue.end - 0.04) or cue.duration < 1.0


def _shots(tmp_path, fingerprint=None, wrap=True):
    observation = {"id": "obs_1", "kind": "scene_detection", "source": "media-analysis/scenes@0.1.0",
                   "data": {"cuts": [{"frame": 38, "time": CUT, "score": 100.0}], "cut_count": 1,
                            "scenes": [{"index": 0, "start": 0.0, "end": CUT}, {"index": 1, "start": CUT, "end": 3.0}]}}
    if fingerprint is not None:
        observation["asset"] = {"path": "/x/in.mp4", "fingerprint": fingerprint, "size": 1}
    doc = {"schema": "media-analysis/response@1", "status": "ok",
           "results": [{"kind": "scene_detection", "status": "ok", "observation": observation}]} if wrap else observation
    (tmp_path / "shots.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path / "shots.json"


def test_shots_are_read_from_a_media_analysis_response_or_observation(tmp_path):
    assert read_shots(_shots(tmp_path), "abc") == [CUT]
    assert read_shots(_shots(tmp_path, wrap=False), "abc") == [CUT]
    assert read_shots(_shots(tmp_path, fingerprint="abc"), "abc") == [CUT]
    with pytest.raises(SubtitleSkillError) as exc:
        read_shots(_shots(tmp_path, fingerprint="other"), "abc")
    assert exc.value.details["field"] == "cuts.shots"
    (tmp_path / "bad.json").write_text(json.dumps({"kind": "silence", "data": {}}), encoding="utf-8")
    with pytest.raises(SubtitleSkillError):
        read_shots(tmp_path / "bad.json", "abc")


# --- emphasis: the rule ---------------------------------------------------------------------

def test_auto_prefers_a_number_then_a_name_then_a_contrast_word():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "but Acme paid $5,000 for it"},
               {"id": "b", "start": 2, "end": 4, "text": "we met Dana but"},
               {"id": "c", "start": 4, "end": 6, "text": "it is not that bad"},
               {"id": "d", "start": 6, "end": 8, "text": "um so like you know"})
    plan = plan_emphasis(doc, parse_emphasis("auto"))
    assert [(m["cue_id"], m["word"], m["reason"]) for m in plan["marks"]] == [
        ("a", "$5,000", "number or amount"),
        ("b", "Dana", "proper name (capitalised mid-sentence)"),
        ("c", "not", "contrast word"),
    ]


def test_a_long_cue_gets_two_marks_and_a_short_one_one():
    doc = _doc({"id": "a", "start": 0, "end": 4, "text": "In 2024 Acme and Dana raised $5,000 but spent it all"},
               {"id": "b", "start": 4, "end": 6, "text": "Acme raised $5,000"})
    plan = plan_emphasis(doc, parse_emphasis("auto"))
    assert [m["word"] for m in plan["marks"] if m["cue_id"] == "a"] == ["2024", "$5,000"]
    assert [m["word"] for m in plan["marks"] if m["cue_id"] == "b"] == ["$5,000"]


def test_numbers_mode_marks_only_numbers():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "Ask Dana but not now"},
               {"id": "b", "start": 2, "end": 4, "text": "up 40% today"})
    plan = plan_emphasis(doc, parse_emphasis("numbers"))
    assert [(m["cue_id"], m["word"]) for m in plan["marks"]] == [("b", "40%")]


def test_explicit_words_round_trip_and_never_mark_a_filler():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "so we really shipped it", "speaker": "Host"})
    auto = plan_emphasis(doc, parse_emphasis("auto"))
    assert auto["marks"] == []
    spec = parse_emphasis({"mode": "words", "words": [{"cue_id": "a", "word": "shipped"}, "really", "Host"]})
    plan = plan_emphasis(doc, spec)
    assert [(m["word"], m["index"]) for m in plan["marks"]] == [("shipped", 4)]
    assert plan["skipped"][0]["reason"] == "filler word, never highlighted"
    again = plan_emphasis(doc, parse_emphasis({"mode": "words", "words": plan["marks"]}))
    assert again["marks"] == [{**m, "reason": "requested"} for m in plan["marks"]]


@pytest.mark.parametrize("raw", ["loud", {"mode": "auto", "words": ["x"]}, {"mode": "words"},
                                 {"mode": "auto", "color": "red"}, {"mode": "auto", "bogus": 1}])
def test_bad_emphasis_is_refused(raw):
    with pytest.raises(SubtitleSkillError) as exc:
        parse_emphasis(raw)
    assert exc.value.code == "INVALID_INPUT"


def test_the_ass_marks_the_word_and_sets_the_cue_back():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "We raised $5,000, today", "style": {"color": "00FF00"}})
    ass = generate_ass(doc, width=320, height=180, look=Look(marks={"a": {2}}))
    assert "We raised {\\c&H00D2FF&\\b1}$5,000{\\c&H00FF00&\\b0}, today" in ass


def test_without_a_look_the_ass_is_the_one_written_before():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "We raised $5,000"})
    plain = generate_ass(doc, width=320, height=180)
    assert plain == generate_ass(doc, width=320, height=180, look=None, wrap=False)
    assert "WrapStyle: 2" in plain and "\\p1" not in plain and "Dialogue: 0," in plain


# --- render: nothing changes without the fields --------------------------------------------

def test_without_the_fields_the_engine_call_and_identity_are_unchanged(tmp_path, monkeypatch):
    """No field, or `emphasis`/`legibility` "off": the same caption argv, no new identity
    keys, and the second render reuses the first."""
    import subtitle_skill.operations as ops
    from test_engine_style import _write_fake_scripts

    root = tmp_path / "ffskill"
    _write_fake_scripts(root)
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    payloads = []
    real = ops.compute_identity

    def spy(**kwargs):
        payloads.append(kwargs["payload"])
        return real(**kwargs)

    monkeypatch.setattr(ops, "compute_identity", spy)
    (tmp_path / "in.mp4").write_bytes(b"fake input video")
    request = {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
               "video_input": "in.mp4", "subtitle": {"id": "d", "language": "en", "cues": [
                   {"id": "c1", "start": 0.0, "end": 2.0, "text": "five thousand dollars"}]}}
    first = execute(request)
    sidecar = json.loads((tmp_path / "out.mp4.subtitle-skill.json").read_text(encoding="utf-8"))
    argv = sidecar["engine_response"]["recorded_argv"]
    assert argv[1:3] == ["--srt", str(tmp_path / "out.subtitle-skill-src.srt")]
    assert set(payloads[0]) == {"document", "format", "constraints", "video_sha256", "engine_script_sha256", "mode",
                                "audio_stream"}
    for key in ("cuts", "spoken_numbers", "emphasis", "legibility"):
        assert key not in first
    again = execute({**request, "emphasis": "off", "legibility": "off"})
    assert again["reused"] is True and payloads[1] == payloads[0]


def test_spoken_numbers_without_a_space_keep_the_caption_srt_path(tmp_path, monkeypatch):
    from test_engine_style import _write_fake_scripts

    root = tmp_path / "ffskill"
    _write_fake_scripts(root)
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    (tmp_path / "in.mp4").write_bytes(b"fake input video")
    request = {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
               "video_input": "in.mp4", "spoken_numbers": "en", "subtitle": {"id": "d", "language": "en", "cues": [
                   {"id": "c1", "start": 0.0, "end": 2.0, "text": "five thousand dollars"}]}}
    response = execute(request)
    sidecar = json.loads((tmp_path / "out.mp4.subtitle-skill.json").read_text(encoding="utf-8"))
    assert sidecar["engine_response"]["recorded_argv"][1] == "--srt"
    assert response["spoken_numbers"]["replacements"][0]["written"] == "$5,000"


# --- render: refusals -------------------------------------------------------------------------

def _render_request(tmp_path, text="We raised five thousand dollars from Acme", start=0.4, end=2.6, **extra):
    return {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
            "video_input": "in.mp4", "subtitle": {"id": "d", "language": "en", "cues": [
                {"id": "c1", "start": start, "end": end, "text": text}]}, **extra}


@pytest.mark.parametrize("extra,field,conflicts", [
    ({"emphasis": "auto", "mode": "mux"}, "emphasis", ["mode"]),
    ({"legibility": "auto", "mode": "mux"}, "legibility", ["mode"]),
    ({"emphasis": "auto", "animate": "fade"}, "animate", ["emphasis"]),
    ({"legibility": "auto", "karaoke": "sweep"}, "karaoke", ["legibility"]),
])
def test_conflicting_fields_are_refused_with_the_reason(tmp_path, extra, field, conflicts):
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_render_request(tmp_path, **extra))
    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details["field"] == field and exc.value.details["conflicts_with"] == conflicts


def test_a_spaced_number_with_an_animation_is_refused(tmp_path):
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_render_request(tmp_path, text="two point five million dollars", spoken_numbers="en", animate="pop"))
    assert exc.value.details["conflicts_with"] == ["spoken_numbers"]


@pytest.mark.parametrize("cuts", [{}, {"shots": "s.json", "detect": True}, {"detect": "yes"}, {"shots": "s.json",
                                  "threshold": 5}, {"detect": True, "threshold": 500}, {"detect": True, "x": 1}])
def test_bad_cuts_are_refused(tmp_path, cuts):
    (tmp_path / "s.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_render_request(tmp_path, cuts=cuts))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"].startswith("cuts")


def test_bad_legibility_is_refused(tmp_path):
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_render_request(tmp_path, legibility="always"))
    assert exc.value.details["field"] == "legibility"


# --- render: real burns, decoded frames -------------------------------------------------------

@pytest.fixture
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        if os.environ.get("SUBTITLE_SKILL_REQUIRE_REAL_ENGINE") == "1":
            pytest.fail("SUBTITLE_SKILL_REQUIRE_REAL_ENGINE=1 but ffmpeg is not on PATH")
        pytest.skip("real ffmpeg is required for render tests")
    return exe


def _clip(path, *colours, shot=1.5):
    """One shot per colour, `shot` seconds each, joined by hard cuts."""
    ffmpeg = _ffmpeg()
    argv = [ffmpeg, "-y", "-v", "error"]
    for colour in colours:
        argv += ["-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:d={shot}:r={FPS}"]
    graph = "".join(f"[{i}]" for i in range(len(colours))) + f"concat=n={len(colours)}:v=1:a=0"
    subprocess.run(argv + ["-filter_complex", graph, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)


class Frames:
    def __init__(self, path):
        data = subprocess.run([_ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              capture_output=True, check=True).stdout
        size = W * H * 3
        self.frames = [data[i:i + size] for i in range(0, len(data), size)]

    def at(self, t):
        return self.frames[round(t * FPS)]

    def pixels(self, t, box=(0, 0, W, H)):
        x0, y0, w, h = box
        frame = self.at(t)
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                i = (y * W + x) * 3
                yield frame[i], frame[i + 1], frame[i + 2]

    def yellow(self, t):
        return sum(1 for r, g, b in self.pixels(t) if r > 200 and g > 150 and b < 110)

    def white(self, t):
        return sum(1 for r, g, b in self.pixels(t) if r > 215 and g > 215 and b > 215)

    def mean(self, t, box):
        values = [(r + g + b) / 3 for r, g, b in self.pixels(t, box)]
        return sum(values) / len(values)


def test_a_cue_across_a_hard_cut_is_split_there_on_the_burned_frames(tmp_path, ffmpeg_skill_install):
    _clip(tmp_path / "cut.mp4", DARK, BRIGHT)
    response = execute(_render_request(tmp_path, video_input="cut.mp4", cuts={"detect": True},
                                       spoken_numbers="en", emphasis="numbers"))
    change = response["cuts"]["changes"][0]
    assert response["cuts"]["cut_times"] == [CUT]
    assert change["action"] == "split" and [p["text"] for p in change["to"]] == ["We raised $5,000", "from Acme"]
    frames = Frames(tmp_path / "out.mp4")
    assert frames.yellow(CUT - 0.04) > 20      # the first part, with its highlighted amount, up to the cut
    assert frames.yellow(CUT) == 0             # and not one frame past it
    assert frames.white(CUT) > 20 and frames.yellow(2.0) == 0  # the second part is drawn from the cut on
    assert response["emphasis"]["marks"] == [
        {"cue_id": "c1.a", "word": "$5,000", "index": 2, "reason": "number or amount"}]


def test_shots_from_media_analysis_give_the_same_split(tmp_path, ffmpeg_skill_install):
    _clip(tmp_path / "in.mp4", DARK, BRIGHT)
    _shots(tmp_path)
    response = execute(_render_request(tmp_path, cuts={"shots": "shots.json"}))
    assert response["cuts"]["source"] == "shots" and response["cuts"]["cut_times"] == [CUT]
    assert [p["id"] for p in response["cuts"]["changes"][0]["to"]] == ["c1.a", "c1.b"]
    assert response["timeline"]["cue_count"] == 2


def test_legibility_adds_a_scrim_only_on_the_bright_shot(tmp_path, ffmpeg_skill_install):
    _clip(tmp_path / "in.mp4", DARK, BRIGHT)
    response = execute(_render_request(tmp_path, cuts={"detect": True}, legibility="auto"))
    by_cue = {c["cue_id"]: c for c in response["legibility"]["cues"]}
    assert by_cue["c1.a"]["treatment"] == "none" and by_cue["c1.a"]["contrast"] > 15
    assert by_cue["c1.b"]["treatment"] == "scrim" and by_cue["c1.b"]["contrast"] < 1.5
    assert by_cue["c1.b"]["contrast_with_scrim"] >= 4.5
    frames = Frames(tmp_path / "out.mp4")
    x, y, w, h = by_cue["c1.b"]["box"]
    # the box is darkened (bright background 240) and the white text still stands out of it
    corner = (x + 1, y + 1, 3, 3)
    assert frames.mean(2.0, corner) < 120
    outside = (x + w + 4, y, 6, h)
    assert frames.mean(2.0, outside) > 230
    assert frames.white(2.0) > 50


def test_clean_footage_is_left_exactly_as_it_was(tmp_path, ffmpeg_skill_install):
    """On a dark picture the treatment is none, and the burn is the same burn as without
    `legibility` (both through this skill's ASS, which emphasis needs)."""
    _clip(tmp_path / "in.mp4", DARK, DARK)
    plain = execute(_render_request(tmp_path, emphasis="numbers", spoken_numbers="en"))
    plain_frames = Frames(tmp_path / "out.mp4").frames
    (tmp_path / "out.mp4").unlink()
    checked = execute(_render_request(tmp_path, emphasis="numbers", spoken_numbers="en", legibility="auto"))
    assert [c["treatment"] for c in checked["legibility"]["cues"]] == ["none"]
    assert Frames(tmp_path / "out.mp4").frames == plain_frames
    assert plain["emphasis"]["marks"][0]["word"] == "$5,000"


def test_a_halo_on_a_mid_grey_picture(tmp_path, ffmpeg_skill_install):
    _clip(tmp_path / "in.mp4", "0x808080", "0x808080")
    response = execute(_render_request(tmp_path, legibility="auto"))
    cue = response["legibility"]["cues"][0]
    assert cue["treatment"] == "halo" and 3.0 <= cue["contrast"] < 4.5


def test_a_spaced_number_stays_on_one_line_when_the_caption_wraps(tmp_path, ffmpeg_skill_install):
    _clip(tmp_path / "in.mp4", DARK, DARK)
    text = "and so this year the whole team raised two point five million dollars"
    response = execute(_render_request(tmp_path, text=text, spoken_numbers="en", emphasis="numbers"))
    assert response["spoken_numbers"]["replacements"][0]["written"] == f"$2.5{NBSP}million"
    frames = Frames(tmp_path / "out.mp4")
    frame = frames.at(1.5)
    rows = sorted({(i // 3) // W for i in range(0, len(frame), 3)
                   if frame[i] > 200 and frame[i + 1] > 150 and frame[i + 2] < 110})
    assert rows, "the amount is highlighted"
    assert rows[-1] - rows[0] < 20, "the highlighted amount is on one line"
    white_rows = sorted({(i // 3) // W for i in range(0, len(frame), 3)
                         if frame[i] > 215 and frame[i + 1] > 215 and frame[i + 2] > 215})
    assert white_rows[-1] - white_rows[0] > 20, "the caption itself wrapped onto two lines"


def test_shorts_chunks_stay_off_the_cut_and_take_emphasis_and_legibility(tmp_path, ffmpeg_skill_install):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={DARK}:s=180x320:d=1.5:r={FPS}",
                    "-f", "lavfi", "-i", f"color=c={BRIGHT}:s=180x320:d=1.5:r={FPS}", "-filter_complex",
                    "[0][1]concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "in.mp4")],
                   check=True, capture_output=True)
    words = [("We", 0.2, 0.5), ("raised", 0.5, 1.0), ("five", 1.1, 1.3), ("thousand", 1.3, 1.7),
             ("dollars", 1.7, 2.1), ("today", 2.2, 2.6)]
    cue = {"id": "c1", "start": 0.2, "end": 2.6, "text": " ".join(w for w, _s, _e in words),
           "words": [{"text": w, "start": s, "end": e} for w, s, e in words]}
    response = execute({"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
                        "video_input": "in.mp4", "subtitle": {"id": "d", "language": "en", "cues": [cue]},
                        "caption_profile": "shorts", "cuts": {"detect": True}, "spoken_numbers": "en",
                        "emphasis": "auto", "legibility": "auto"})
    chunks = response["caption_profile"]["cues"]
    assert [c["text"] for c in chunks] == ["We raised", "$5,000 today"]
    # "$5,000" is one timed word (1.1-2.1 s): its chunk is not split inside it; it starts at the cut
    assert response["cuts"]["changes"] == [{"cue_id": "c1.2", "action": "start_moved", "cut": CUT,
                                            "from": [1.1, 2.6], "to": [CUT, 2.6]}]
    assert all(not (c["start"] < CUT < c["end"]) for c in chunks)
    assert response["emphasis"]["marks"][0]["word"] == "$5,000"
    treatments = {c["cue_id"]: c["treatment"] for c in response["legibility"]["cues"]}
    assert set(treatments.values()) <= {"none", "halo", "scrim"} and len(treatments) == 2
