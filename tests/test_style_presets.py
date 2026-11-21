"""Style presets as data (docs/adr/ADR-015-style-presets.md, roadmap step 14).

Unit tests for the allowlist and the ASS style line, the conflicts, the "nothing changes
without a preset" guard (golden ASS written by 1.0.0, the caption argv and the identity
keys), and real burns through the vendored caption.py and ffmpeg on a lavfi clip: each
bundled preset's size (line pitch within 10 % of the stated percent of the height), colour,
case and position are read back from the decoded frames.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats.ass import Look, generate_ass
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.operations import execute
from subtitle_skill.style_presets import BUNDLED, FIELDS, PRESET_DIR, load_bundled, resolve_font, validate

FIXTURES = Path(__file__).parent / "fixtures"
VENDOR_ROOT = Path(os.environ.get("SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC") or FIXTURES / "ffmpeg_skill_vendor")
W, H, FPS = 640, 360, 25
GREY = "0x404040"


def _doc(*cues, language="en"):
    return SubtitleDocument.from_dict({"id": "d", "language": language, "cues": list(cues)})


# --- the bundled presets and the allowlist -----------------------------------------------------

def test_every_bundled_preset_is_a_valid_small_json_file_named_after_itself():
    files = sorted(p.stem for p in PRESET_DIR.glob("*.json"))
    assert files == sorted(BUNDLED)
    for name in BUNDLED:
        path = PRESET_DIR / f"{name}.json"
        assert path.stat().st_size < 1024
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw) == FIELDS  # each bundled preset states every field
        preset = load_bundled(name)
        assert preset.name == name and preset.animation == "none" and preset.fonts


def _check(raw):
    with pytest.raises(SubtitleSkillError) as exc:
        validate(raw, source="p.json")
    assert exc.value.code == "INVALID_INPUT"
    return exc.value.details["field"]


@pytest.mark.parametrize("raw,field", [
    ({"name": "x", "colour": "FFFFFF"}, "style_preset.colour"),
    ({"name": "x", "command": "rm -rf /"}, "style_preset.command"),
    ({"name": "x", "path": "fonts/a.ttf"}, "style_preset.path"),
    ({"name": "x", "override": "{\\b1}"}, "style_preset.override"),
    ({"name": "x", "font": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}, "style_preset.font"),
    ({"name": "x", "font": ["DejaVu Sans", "..\\fonts\\a.ttf"]}, "style_preset.font"),
    ({"name": "x", "font": "Sans{\\fnEvil}"}, "style_preset.font"),
    ({"name": "x", "font": "Sans,Serif"}, "style_preset.font"),
    ({"name": "x", "font": []}, "style_preset.font"),
    ({"name": "x", "color": "{\\c&H0000FF&}"}, "style_preset.color"),
    ({"name": "x", "outline_color": "red"}, "style_preset.outline_color"),
    ({"name": "x", "size": 50}, "style_preset.size"),
    ({"name": "x", "size": True}, "style_preset.size"),
    ({"name": "x", "outline": -1}, "style_preset.outline"),
    ({"name": "x", "margin": "5%"}, "style_preset.margin"),
    ({"name": "x", "case": "title"}, "style_preset.case"),
    ({"name": "x", "position": "middle"}, "style_preset.position"),
    ({"name": "x", "box": "opaque"}, "style_preset.box"),
    ({"name": "x", "bold": "yes"}, "style_preset.bold"),
    ({"name": "x", "animation": "fade"}, "style_preset.animation"),
    ({"name": "x", "highlight": {"color": "FFD200", "tags": "\\b1"}}, "style_preset.highlight.tags"),
    ({"name": "x", "highlight": {"color": "nope"}}, "style_preset.highlight.color"),
    ({"name": "x", "highlight": "FFD200"}, "style_preset.highlight"),
    ({"name": "Bad Name"}, "style_preset.name"),
    ({}, "style_preset.name"),
    (["not", "an", "object"], "style_preset"),
])
def test_a_preset_outside_the_allowlist_is_refused_naming_the_key(raw, field):
    assert _check(raw) == field


def test_a_minimal_preset_keeps_the_defaults():
    preset = validate({"name": "mine"}, source="p.json")
    assert (preset.size, preset.color, preset.case, preset.position, preset.box) == (None, None, "none", "bottom", "none")
    assert preset.to_dict()["source"] == "p.json"


def test_the_font_chain_resolves_to_the_first_installed_family():
    chain = ("Missing Family", "DejaVu Sans", "Liberation Sans")
    assert resolve_font(chain, frozenset({"liberation sans", "dejavu sans"})) == ("DejaVu Sans", True)
    assert resolve_font(chain, frozenset()) == ("Missing Family", False)
    assert resolve_font(chain, None) == ("Missing Family", False)  # no fontconfig to ask
    assert resolve_font((), frozenset()) == (None, False)


# --- the ASS style line and precedence --------------------------------------------------------

def _style_line(ass):
    return next(line for line in ass.splitlines() if line.startswith("Style: "))[7:].split(",")


def test_a_preset_sets_the_style_line_in_percent_of_the_height():
    ass = generate_ass(_doc({"id": "a", "start": 0, "end": 2, "text": "hello"}), width=W, height=H,
                       preset=load_bundled("bold-short-form"), font="DejaVu Sans", wrap=True)
    style = _style_line(ass)
    assert style[1] == "DejaVu Sans" and style[2] == str(round(0.09 * H))  # Fontsize 9 % of 360
    assert style[3] == "&H00FFFFFF" and style[5] == "&H00000000" and style[7] == "-1"  # white, black, bold
    assert style[15:19] == ["1", f"{0.009 * H:g}", "0", "2"]  # BorderStyle, Outline, Shadow, bottom centre
    assert style[21] == str(round(0.18 * H))  # MarginV
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,HELLO" in ass and "WrapStyle: 0" in ass


def test_boxed_draws_an_opaque_box_and_lower_third_is_bottom_left():
    boxed = _style_line(generate_ass(_doc({"id": "a", "start": 0, "end": 1, "text": "x"}), width=W, height=H,
                                     preset=load_bundled("boxed")))
    assert boxed[15] == "3" and boxed[5] == "&H00000000"  # BorderStyle 3, the box in outline_color, opaque
    lower = _style_line(generate_ass(_doc({"id": "a", "start": 0, "end": 1, "text": "x"}), width=W, height=H,
                                     preset=load_bundled("clean-lower-third")))
    assert lower[18] == "1"


def test_explicit_style_fields_override_the_preset():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "hello", "style": {"color": "FF0000", "bold": False}})
    from subtitle_skill.models import SubtitleStyle

    ass = generate_ass(doc, width=W, height=H, preset=load_bundled("bold-short-form"), font="Liberation Sans",
                       style=SubtitleStyle(size=5))
    style = _style_line(ass)
    assert style[1] == "Liberation Sans"  # fonts_dir's family (or any explicit font) wins
    assert style[2] == str(round(0.05 * 288 * H / 288))  # style.size wins over the preset's 9 %
    assert "{\\b0\\c&H0000FF&}HELLO" in ass  # the cue's own bold: false and colour win per event


def test_the_preset_highlight_is_drawn_and_its_weight_kept():
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "We raised $5,000"})
    look = Look(marks={"a": {2}}, highlight_color="7FD4FF", highlight_bold=False)
    ass = generate_ass(doc, width=W, height=H, preset=load_bundled("clean-lower-third"), look=look, wrap=True)
    assert "We raised {\\c&HFFD47F&\\b0}$5,000{\\c&HFFFFFF&\\b0}" in ass


@pytest.mark.parametrize("name,kwargs", [
    ("no_preset_placement.ass", {"positions": {"b": "top"}}),
    ("no_preset_emphasis.ass", {"look": "marks", "wrap": True}),
])
def test_without_a_preset_the_ass_is_byte_for_byte_the_one_1_0_0_wrote(name, kwargs):
    """Golden files written by subtitle-skill 1.0.0's generate_ass for the same document."""
    doc = _doc({"id": "a", "start": 0, "end": 2, "text": "We raised $5,000", "style": {"bold": True}},
               {"id": "b", "start": 2, "end": 3.5, "text": "Top {cue}", "style": {"color": "FF0000", "bold": False}})
    if kwargs.get("look") == "marks":
        kwargs = {**kwargs, "look": Look(marks={"a": {2}})}
    expected = (FIXTURES / "presets" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert generate_ass(doc, width=320, height=180, **kwargs) == expected
    if "look" in kwargs:
        kwargs["look"] = Look(marks={"a": {2}})
    assert generate_ass(doc, width=320, height=180, preset=None, **kwargs) == expected


# --- render with a recording fake caption.py --------------------------------------------------

@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    from test_engine_style import _write_fake_scripts

    root = tmp_path / "ffskill"
    _write_fake_scripts(root)
    (root / "scripts" / "probe.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'file': sys.argv[1], 'duration': 10.0, "
        f"'video': {{'codec': 'h264', 'width': {W}, 'height': {H}}}, 'audio': None}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    (tmp_path / "in.mp4").write_bytes(b"fake input video")
    return root


def _request(tmp_path, text="hello there", **extra):
    return {"operation": "render", "workspace": str(tmp_path), "format": "srt", "output_path": "out.mp4",
            "video_input": "in.mp4", "subtitle": {"id": "d", "language": "en", "cues": [
                {"id": "c1", "start": 0.0, "end": 2.0, "text": text}]}, **extra}


def _spy_identity(monkeypatch):
    import subtitle_skill.operations as ops

    payloads = []
    real = ops.compute_identity

    def spy(**kwargs):
        payloads.append(kwargs["payload"])
        return real(**kwargs)

    monkeypatch.setattr(ops, "compute_identity", spy)
    return payloads


def _argv(tmp_path):
    sidecar = json.loads((tmp_path / "out.mp4.subtitle-skill.json").read_text(encoding="utf-8"))
    return sidecar["engine_response"]["recorded_argv"]


def test_without_a_preset_the_engine_call_identity_and_response_are_unchanged(tmp_path, fake_engine, monkeypatch):
    payloads = _spy_identity(monkeypatch)
    first = execute(_request(tmp_path))
    assert _argv(tmp_path)[1:3] == ["--srt", str(tmp_path / "out.subtitle-skill-src.srt")]
    assert set(payloads[0]) == {"document", "format", "constraints", "video_sha256", "engine_script_sha256", "mode",
                                "audio_stream"}
    assert "style_preset" not in first
    again = execute(_request(tmp_path))
    assert again["reused"] is True and payloads[1] == payloads[0]


def test_a_preset_burns_through_the_skills_ass_and_is_in_the_identity(tmp_path, fake_engine, monkeypatch):
    payloads = _spy_identity(monkeypatch)
    plain = execute(_request(tmp_path))
    response = execute(_request(tmp_path, style_preset="minimal-serif"))
    assert response["reused"] is False and plain["reused"] is False
    assert _argv(tmp_path)[1] == "--ass"
    assert payloads[1]["style_preset"]["name"] == "minimal-serif" and "source" not in payloads[1]["style_preset"]
    reported = response["style_preset"]
    assert reported["name"] == "minimal-serif" and reported["source"] == "bundled"
    assert reported["font_family"] in ("DejaVu Serif", "Liberation Serif", "Noto Serif")
    other = execute(_request(tmp_path, style_preset="boxed"))
    assert other["reused"] is False and payloads[2]["style_preset"] != payloads[1]["style_preset"]
    assert execute(_request(tmp_path, style_preset="boxed"))["reused"] is True


def test_a_callers_preset_file_is_read_from_the_workspace(tmp_path, fake_engine):
    (tmp_path / "brand.json").write_text(json.dumps({"name": "brand", "color": "00FF88", "position": "top"}),
                                         encoding="utf-8")
    response = execute(_request(tmp_path, style_preset={"path": "brand.json"}))
    assert response["style_preset"]["source"] == "brand.json" and response["style_preset"]["color"] == "00FF88"


@pytest.mark.parametrize("content,field", [
    ({"name": "brand", "fontfile": "a.ttf"}, "style_preset.fontfile"),
    ({"name": "brand", "font": "../fonts/a.ttf"}, "style_preset.font"),
    ({"name": "brand", "highlight": {"color": "FFD200", "override": "{\\blur9}"}}, "style_preset.highlight.override"),
])
def test_a_callers_bad_preset_file_is_refused_naming_the_key(tmp_path, fake_engine, content, field):
    (tmp_path / "brand.json").write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, style_preset={"path": "brand.json"}))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == field


@pytest.mark.parametrize("value,field", [
    ("neon", "style_preset"),
    ({"path": "brand.json", "name": "x"}, "style_preset"),
    ({"name": "inline"}, "style_preset"),
    (7, "style_preset"),
    ({"path": "not-json.json"}, "style_preset.path"),
])
def test_a_bad_style_preset_value_is_refused(tmp_path, fake_engine, value, field):
    (tmp_path / "not-json.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, style_preset=value))
    assert exc.value.code == "INVALID_INPUT" and exc.value.details["field"] == field


def test_a_preset_path_outside_the_workspace_is_refused(tmp_path, fake_engine):
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, style_preset={"path": "../outside.json"}))
    assert exc.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.parametrize("extra,field,conflicts", [
    ({"mode": "mux"}, "style_preset", ["mode"]),
    ({"animate": "fade"}, "animate", ["style_preset"]),
    ({"karaoke": "sweep"}, "karaoke", ["style_preset"]),
    ({"caption_profile": "shorts"}, "style_preset", ["caption_profile"]),
    ({"legibility": "auto", "style_preset": "boxed"}, "legibility", ["style_preset"]),
])
def test_incompatible_combinations_are_refused_with_conflicts_with(tmp_path, fake_engine, extra, field, conflicts):
    request = _request(tmp_path, **{"style_preset": "clean-lower-third", **extra})
    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details["field"] == field and exc.value.details["conflicts_with"] == conflicts


def test_a_preset_with_ruby_or_a_top_preset_with_placement_is_refused(tmp_path, fake_engine):
    request = _request(tmp_path, style_preset="boxed")
    request["subtitle"]["cues"][0]["ruby"] = [{"base": "hello", "text": "heh-loh"}]
    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.details["conflicts_with"] == ["ruby"]
    (tmp_path / "top.json").write_text(json.dumps({"name": "top", "position": "top"}), encoding="utf-8")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, style_preset={"path": "top.json"}, placement={"avoid": []}))
    assert exc.value.details["conflicts_with"] == ["placement"]


def test_animate_none_is_allowed_with_a_preset(tmp_path, fake_engine):
    response = execute(_request(tmp_path, style_preset="boxed", animate="none"))
    assert "animation" not in response and response["style_preset"]["name"] == "boxed"


# --- real burns, decoded frames ---------------------------------------------------------------

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


def _clip(path):
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={GREY}:s={W}x{H}:d=2:r={FPS}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


def _frame(path, t=1.0):
    return subprocess.run([_ffmpeg(), "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                           "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _ink(frame, colour, tolerance=40):
    """(x, y) of every pixel within `tolerance` of `colour` on each channel."""
    target = _rgb(colour)
    out = []
    for i in range(0, W * H * 3, 3):
        if all(abs(frame[i + k] - target[k]) < tolerance for k in range(3)):
            out.append(((i // 3) % W, (i // 3) // W))
    return out


def _bands(points):
    """Rows with ink, grouped into [top, bottom] bands (one per drawn line)."""
    rows = sorted({y for _x, y in points})
    bands = []
    for y in rows:
        if bands and y == bands[-1][1] + 1:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    return bands


def _burn(tmp_path, text, **extra):
    _clip(tmp_path / "in.mp4")
    request = _request(tmp_path, text=text, **extra)
    response = execute(request)
    return response, _frame(tmp_path / "out.mp4")


@pytest.mark.parametrize("name", BUNDLED)
def test_each_bundled_preset_burns_its_size_colour_case_and_position(tmp_path, ffmpeg_skill_install, name):
    preset = load_bundled(name)
    # two lines of one glyph: the band tops give the line pitch (the font size libass uses,
    # whatever the family) and a band's height tells capitals from lower case
    response, frame = _burn(tmp_path, "oooo\noooo", style_preset=name)
    assert response["style_preset"]["name"] == name
    ink = _ink(frame, preset.color)
    bands = _bands(ink)
    assert len(bands) == 2, bands
    pitch = bands[1][0] - bands[0][0]
    stated = preset.size / 100 * H
    assert abs(pitch - stated) <= 0.1 * stated, (pitch, stated)                # size
    assert len(ink) > 150                                                       # colour
    glyph = (bands[0][1] - bands[0][0] + 1) / pitch
    assert (glyph > 0.55) if preset.case == "upper" else (glyph < 0.5), glyph   # case: O or o
    xs = [x for x, _y in ink]
    bottom = bands[1][1]
    margin = preset.margin / 100 * H
    if preset.position == "lower-third":                                        # position
        assert min(xs) < 0.1 * W and max(xs) < W / 2
    else:
        assert abs((min(xs) + max(xs)) / 2 - W / 2) < 0.05 * W
    assert H - margin - 0.5 * pitch < bottom < H - margin + 0.1 * pitch, (bottom, H - margin)
    if preset.box == "scrim":
        # an opaque box in outline_color around the text, over the grey picture
        box = _rgb(preset.outline_color)
        y = bands[0][0] - 2
        row = [frame[(y * W + x) * 3:(y * W + x) * 3 + 3] for x in range(min(xs), max(xs) + 1)]
        assert all(max(abs(p[k] - box[k]) for k in range(3)) < 30 for p in row)
    else:
        y = bands[0][0] - 3
        assert frame[(y * W + W // 2) * 3] > 50  # the picture, not a box


def test_a_callers_top_preset_burns_at_the_top_in_its_colour(tmp_path, ffmpeg_skill_install):
    (tmp_path / "brand.json").write_text(json.dumps({"name": "brand", "color": "00FF88", "size": 8, "margin": 5,
                                                     "position": "top", "case": "upper"}), encoding="utf-8")
    response, frame = _burn(tmp_path, "oooo", style_preset={"path": "brand.json"})
    bands = _bands(_ink(frame, "00FF88"))
    assert len(bands) == 1 and bands[0][0] < 0.05 * H + 0.08 * H * 0.5
    assert response["style_preset"]["source"] == "brand.json"


def test_a_preset_with_emphasis_draws_the_presets_highlight(tmp_path, ffmpeg_skill_install):
    response, frame = _burn(tmp_path, "We raised $5,000 today", style_preset="clean-lower-third", emphasis="numbers")
    assert response["emphasis"]["color"] == "7FD4FF"
    assert len(_ink(frame, "7FD4FF", tolerance=35)) > 30   # the preset's light blue
    assert len(_ink(frame, "FFD200", tolerance=35)) == 0   # not the default yellow


def test_an_explicit_emphasis_colour_wins_over_the_presets_highlight(tmp_path, ffmpeg_skill_install):
    response, frame = _burn(tmp_path, "We raised $5,000 today", style_preset="clean-lower-third",
                            emphasis={"mode": "numbers", "color": "FF3030"})
    assert response["emphasis"]["color"] == "FF3030"
    assert len(_ink(frame, "FF3030", tolerance=40)) > 30 and len(_ink(frame, "7FD4FF", tolerance=35)) == 0


def test_an_explicit_cue_size_wins_over_the_presets_size(tmp_path, ffmpeg_skill_install):
    request_text = "oooo\noooo"
    _clip(tmp_path / "in.mp4")
    request = _request(tmp_path, text=request_text, style_preset="bold-short-form")
    request["subtitle"]["cues"][0]["style"] = {"size": 5}
    execute(request)
    bands = _bands(_ink(_frame(tmp_path / "out.mp4"), "FFFFFF"))
    pitch = bands[1][0] - bands[0][0]
    assert abs(pitch - 0.05 * H) <= 0.1 * 0.05 * H, pitch
