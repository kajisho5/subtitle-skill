"""`request.fonts_dir` (docs/adr/ADR-010-fonts-dir.md): the caller's own font
files for a burn on a machine where no installed font covers the subtitle.

Three layers:
- `subtitle_skill.fonts` reading family names and character coverage from font
  files written by this test (no font binary is committed);
- `render` against a FAKE caption/probe pair that records its argv (the same
  pattern as tests/test_engine_style.py): pass-through, validation, the
  caption tool that has no such flag, and the guided error;
- `render` against the real ffmpeg-skill `caption` and a real font, under a
  fontconfig that has no Japanese font (Linux, needs ffmpeg and an installed
  Japanese font to copy from; skipped otherwise, or failed when
  SUBTITLE_SKILL_REQUIRE_REAL_ENGINE=1 says the machine was set up for it).
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from subtitle_skill import fonts
from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.operations import execute

VENDOR_ROOT = Path(
    os.environ.get("SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC")
    or Path(__file__).parent / "fixtures" / "ffmpeg_skill_vendor"
)

#: The refusal exactly as ffmpeg-skill's caption tool words it (its
#: `_common/fonts.py`, read at 2.24.0, 3.0.0, 3.2.3 and 3.3.0).
ENGINE_FONT_REFUSAL = (
    "no installed font covers Japanese text on this machine \u2014 install fonts-noto-cjk / fonts-noto-core (apt), "
    "brew install --cask font-noto-sans-cjk / font-noto-sans-arabic (mac), or pass a font file "
    "(--font-file on overlay.py/graphics.py, --fonts-dir with --font on caption.py)"
)

JA_TEXT = "\u5b57\u5e55\u306e\u30c6\u30b9\u30c8"  # 字幕のテスト
JA_RANGES = [(0x3040, 0x30FF), (0x4E00, 0x9FFF)]
LATIN_RANGES = [(0x20, 0x7E)]


# --- font files written by the test ------------------------------------------


def _name_table(family: str, *, language: int = 0x0409) -> bytes:
    raw = family.encode("utf-16-be")
    return struct.pack(">HHH", 0, 1, 18) + struct.pack(">HHHHHH", 3, 1, language, 1, len(raw), 0) + raw


def _cmap12(ranges) -> bytes:
    groups = b"".join(struct.pack(">III", start, end, 1) for start, end in ranges)
    sub = struct.pack(">HHIII", 12, 0, 16 + len(groups), 0, len(ranges)) + groups
    return struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12) + sub


def _cmap4(ranges) -> bytes:
    segments = list(ranges) + [(0xFFFF, 0xFFFF)]
    n = len(segments)
    body = (
        struct.pack(f">{n}H", *[end for _start, end in segments])
        + struct.pack(">H", 0)
        + struct.pack(f">{n}H", *[start for start, _end in segments])
        + struct.pack(f">{n}h", *([1] * n))
        + struct.pack(f">{n}H", *([0] * n))
    )
    sub = struct.pack(">HHHHHHH", 4, 14 + len(body), 0, 2 * n, 0, 0, 0) + body
    return struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + sub


def _sfnt(tables: dict, *, base: int = 0) -> bytes:
    """An sfnt with just `tables`; `base` is where it will sit in its file
    (table offsets are from the start of the file, also inside a collection)."""
    head = b"\x00\x01\x00\x00" + struct.pack(">HHHH", len(tables), 0, 0, 0)
    offset = base + 12 + 16 * len(tables)
    directory, body = b"", b""
    for tag, data in tables.items():
        directory += struct.pack(">4sIII", tag, 0, offset, len(data))
        body += data
        offset += len(data)
    return head + directory + body


def _write_font(path: Path, family: str, ranges, *, cmap=_cmap12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_sfnt({b"cmap": cmap(ranges), b"name": _name_table(family)}))
    return path


def _write_collection(path: Path, members) -> Path:
    """A TrueType collection of `(family, ranges)` faces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header_size = 12 + 4 * len(members)
    blobs, offsets, at = [], [], header_size
    for family, ranges in members:
        blob = _sfnt({b"cmap": _cmap12(ranges), b"name": _name_table(family)}, base=at)
        blobs.append(blob)
        offsets.append(at)
        at += len(blob)
    path.write_bytes(b"ttcf" + struct.pack(">II", 0x00010000, len(members)) + struct.pack(f">{len(members)}I", *offsets)
                     + b"".join(blobs))
    return path


# --- subtitle_skill.fonts -----------------------------------------------------


def test_scan_reads_family_and_coverage(tmp_path):
    _write_font(tmp_path / "b-ja.ttf", "Example Gothic", JA_RANGES)
    _write_font(tmp_path / "a-latin.otf", "Example Sans", LATIN_RANGES, cmap=_cmap4)
    (tmp_path / "notes.txt").write_text("not a font", encoding="utf-8")
    (tmp_path / "broken.ttf").write_bytes(b"\x00\x01\x00\x00 not really a font")
    (tmp_path / "sub").mkdir()
    _write_font(tmp_path / "sub" / "deeper.ttf", "Never Read", JA_RANGES)

    faces = fonts.scan(tmp_path)
    assert [(f.file, f.family) for f in faces] == [("a-latin.otf", "Example Sans"), ("b-ja.ttf", "Example Gothic")]
    latin, ja = faces
    assert latin.covers(ord("A")) and not latin.covers(ord("\u5b57")) and not latin.covers(0xFFFF)
    assert ja.covers(ord("\u5b57")) and ja.covers(ord("\u306e")) and not ja.covers(ord("A"))


def test_scan_reads_every_face_of_a_collection(tmp_path):
    _write_collection(tmp_path / "pan.ttc", [("Example CJK JP", JA_RANGES), ("Example CJK KR", [(0xAC00, 0xD7A3)])])
    faces = fonts.scan(tmp_path)
    assert [(f.file, f.index, f.family) for f in faces] == [("pan.ttc", 0, "Example CJK JP"), ("pan.ttc", 1, "Example CJK KR")]
    assert faces[1].covers(0xD55C) and not faces[0].covers(0xD55C)


def test_a_family_that_cannot_be_named_in_a_style_is_not_offered(tmp_path):
    _write_font(tmp_path / "comma.ttf", "Example, Inc Gothic", JA_RANGES)
    assert fonts.scan(tmp_path) == []


def test_choose_takes_the_face_with_the_most_of_the_text(tmp_path):
    _write_font(tmp_path / "a-latin.ttf", "Example Sans", LATIN_RANGES)
    _write_font(tmp_path / "b-ja.ttf", "Example Gothic", JA_RANGES)
    _write_font(tmp_path / "c-ja-bold.ttf", "Example Gothic", JA_RANGES)
    choice = fonts.choose(fonts.scan(tmp_path), fonts.text_characters([JA_TEXT + " ok"]))
    assert choice.family == "Example Gothic"
    assert choice.files == ("b-ja.ttf", "c-ja-bold.ttf")
    assert choice.missing == ("o", "k")


def test_choose_prefers_the_named_family_between_equals(tmp_path):
    _write_collection(tmp_path / "pan.ttc", [("Example CJK KR", JA_RANGES), ("Example CJK JP", JA_RANGES)])
    characters = fonts.text_characters([JA_TEXT])
    assert fonts.choose(fonts.scan(tmp_path), characters).family == "Example CJK KR"
    assert fonts.choose(fonts.scan(tmp_path), characters, preferred="example cjk jp").family == "Example CJK JP"


def test_choose_refuses_a_directory_without_the_script(tmp_path):
    _write_font(tmp_path / "latin.ttf", "Example Sans", LATIN_RANGES)
    with pytest.raises(SubtitleSkillError) as exc:
        fonts.choose(fonts.scan(tmp_path), fonts.text_characters([JA_TEXT]))
    assert exc.value.code == "INVALID_INPUT"
    assert "fonts_dir" in exc.value.message


def test_choose_refuses_a_directory_without_a_readable_font(tmp_path):
    (tmp_path / "readme.ttf").write_bytes(b"plain text")
    with pytest.raises(SubtitleSkillError) as exc:
        fonts.choose(fonts.scan(tmp_path), fonts.text_characters(["hello"]))
    assert exc.value.code == "INVALID_INPUT"


def test_text_characters_asks_for_letters_digits_and_punctuation_only():
    assert fonts.text_characters(["a b\n1!", "a\u200d\u2764\ufe0f"]) == ["a", "b", "1", "!"]


def test_digest_follows_the_font_files(tmp_path):
    _write_font(tmp_path / "ja.ttf", "Example Gothic", JA_RANGES)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    first = fonts.digest(tmp_path)
    (tmp_path / "notes.txt").write_text("y", encoding="utf-8")
    assert fonts.digest(tmp_path) == first
    _write_font(tmp_path / "ja.ttf", "Example Gothic", JA_RANGES + [(0xFF00, 0xFFEF)])
    assert fonts.digest(tmp_path) != first


# --- render against a fake caption tool ---------------------------------------

_FAKE_PROBE = """
import json, sys
print(json.dumps({"file": sys.argv[1], "duration": 10.0,
                  "video": {"codec": "h264", "width": 640, "height": 360}, "audio": None}))
"""

#: Records its argv and the subtitle file it was given. `{flags}` is where the
#: argument names live that `engine.caption_takes_fonts_dir` looks for;
#: `{refusal}` makes it fail the way the real tool does.
_FAKE_CAPTION = """
import json, sys
FLAGS = {flags!r}
REFUSAL = {refusal!r}
def main():
    args = sys.argv[1:]
    if REFUSAL is not None and "--fonts-dir" not in args:
        print(json.dumps({{"status": "failed", "exit_code": 1,
                          "error": {{"kind": "input", "message": REFUSAL}}, "commands": []}}))
        return 1
    recorded = [a for a in args if a != "--json"]
    output_path = args[args.index("-o") + 1]
    subtitle = None
    for flag in ("--srt", "--ass"):
        if flag in args:
            with open(args[args.index(flag) + 1], encoding="utf-8") as f:
                subtitle = f.read()
    with open(output_path, "wb") as f:
        f.write(b"fake mp4 bytes")
    probe = {{"file": output_path, "duration": 10.0, "video": {{"codec": "h264"}}, "audio": None}}
    print(json.dumps({{"status": "completed", "output": output_path, "dry_run": False, "commands": ["fake"],
                      "probe": probe, "recorded_argv": recorded, "recorded_subtitle": subtitle}}))
    return 0
if __name__ == "__main__":
    sys.exit(main())
"""

_WITH_FLAGS = ('"--fonts-dir"', '"--font"')


def _install_fake(tmp_path, monkeypatch, *, flags=_WITH_FLAGS, refusal=None) -> Path:
    root = tmp_path / "ffskill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "_common.py").write_text("# fake\n", encoding="utf-8")
    (root / "scripts" / "probe.py").write_text(textwrap.dedent(_FAKE_PROBE), encoding="utf-8")
    (root / "scripts" / "caption.py").write_text(
        textwrap.dedent(_FAKE_CAPTION).format(flags=flags, refusal=refusal), encoding="utf-8"
    )
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
    return root


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    return _install_fake(tmp_path, monkeypatch)


def _workspace(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "in.mp4").write_bytes(b"fake input video")
    return ws


def _request(ws: Path, **extra) -> dict:
    request = {
        "operation": "render",
        "workspace": str(ws),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": {"id": "d", "language": "ja", "cues": [{"id": "c1", "start": 0, "end": 2, "text": JA_TEXT}]},
    }
    request.update(extra)
    return request


def _engine_response(ws: Path, name: str = "out.mp4") -> dict:
    return json.loads((ws / f"{name}.subtitle-skill.json").read_text(encoding="utf-8"))["engine_response"]


def test_fonts_dir_is_passed_to_caption_with_the_family_it_holds(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)

    response = execute(_request(ws, fonts_dir="fonts"))

    assert response["status"] == "ok"
    argv = _engine_response(ws)["recorded_argv"]
    at = argv.index("--fonts-dir")
    assert argv[at:at + 4] == ["--fonts-dir", str((ws / "fonts").resolve()), "--font", "Example Gothic"]
    assert "--srt" in argv
    # provenance: the path as the request gave it, what was read from it, no bytes
    assert response["fonts_dir"] == {"path": "fonts", "family": "Example Gothic", "files": ["ja.ttf"]}
    sidecar = json.loads((ws / "out.mp4.subtitle-skill.json").read_text(encoding="utf-8"))
    assert sidecar["fonts_dir"] == response["fonts_dir"]
    assert response["observation"] == []


def test_without_fonts_dir_nothing_changes(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    response = execute(_request(ws))
    argv = _engine_response(ws)["recorded_argv"]
    assert "--fonts-dir" not in argv and "--font" not in argv
    assert "fonts_dir" not in response


def test_fonts_dir_family_is_named_in_the_ass_style(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)
    request = _request(ws, fonts_dir="fonts")
    request["subtitle"]["cues"][0]["ruby"] = [{"base": "\u5b57\u5e55", "text": "\u3058\u307e\u304f"}]

    response = execute(request)

    recorded = _engine_response(ws)
    assert "--ass" in recorded["recorded_argv"]
    assert "Style: Default,Example Gothic," in recorded["recorded_subtitle"]
    # caption.py is told the font is the caller's, so its installed-font check does not refuse the ASS
    at = recorded["recorded_argv"].index("--fonts-dir")
    assert recorded["recorded_argv"][at + 2:at + 4] == ["--font", "Example Gothic"]
    assert response["fonts_dir"]["family"] == "Example Gothic"


def test_ass_style_keeps_its_own_family_without_fonts_dir(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    request = _request(ws)
    request["subtitle"]["cues"][0]["ruby"] = [{"base": "\u5b57\u5e55", "text": "\u3058\u307e\u304f"}]
    execute(request)
    assert "Style: Default,Noto Sans CJK JP," in _engine_response(ws)["recorded_subtitle"]


def test_fonts_dir_is_reported_again_on_reuse_and_a_changed_font_is_a_new_render(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)
    first = execute(_request(ws, fonts_dir="fonts"))
    again = execute(_request(ws, fonts_dir="fonts"))
    assert first["reused"] is False and again["reused"] is True
    assert again["fonts_dir"] == first["fonts_dir"]

    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES + [(0xFF00, 0xFFEF)])
    assert execute(_request(ws, fonts_dir="fonts"))["reused"] is False
    # and a render with no fonts_dir is not the render that had one
    assert execute(_request(ws))["reused"] is False


def test_a_character_the_family_lacks_is_reported(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "kana.ttf", "Example Kana", [(0x3040, 0x30FF)])
    response = execute(_request(ws, fonts_dir="fonts"))
    assert response["status"] == "ok"
    (issue,) = response["observation"]
    assert issue["code"] == "FONT_GLYPHS_MISSING" and issue["severity"] == "warning"
    assert "\u5b57\u5e55" in issue["message"] and "Example Kana" in issue["message"]


@pytest.mark.parametrize(
    "value, code",
    [
        ("/etc/fonts", "PATH_NOT_ALLOWED"),
        ("C:\\Windows\\Fonts", "PATH_NOT_ALLOWED"),
        ("../outside", "PATH_NOT_ALLOWED"),
        ("fonts/../../outside", "PATH_NOT_ALLOWED"),
        ("missing", "MISSING_INPUT"),
        ("in.mp4", "INVALID_INPUT"),
        ("", "INVALID_INPUT"),
        (["fonts"], "INVALID_INPUT"),
        (7, "INVALID_INPUT"),
    ],
)
def test_fonts_dir_goes_through_the_path_policy(tmp_path, fake_engine, value, code):
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    _write_font(outside / "ja.ttf", "Example Gothic", JA_RANGES)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws, fonts_dir=value))
    assert exc.value.code == code
    assert not (ws / "out.mp4").exists()


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs a privilege on Windows")
def test_fonts_dir_symlink_out_of_the_workspace_is_refused(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    _write_font(outside / "ja.ttf", "Example Gothic", JA_RANGES)
    (ws / "fonts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws, fonts_dir="fonts"))
    assert exc.value.code == "PATH_NOT_ALLOWED"
    assert not (ws / "out.mp4").exists()


def test_fonts_dir_without_a_font_for_the_text_is_refused_before_the_engine_runs(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    (ws / "empty").mkdir()
    _write_font(ws / "latin" / "latin.ttf", "Example Sans", LATIN_RANGES)
    for name in ("empty", "latin"):
        with pytest.raises(SubtitleSkillError) as exc:
            execute(_request(ws, fonts_dir=name))
        assert exc.value.code == "INVALID_INPUT"
        assert "fonts_dir" in exc.value.message
    assert not (ws / "out.mp4").exists()


def test_fonts_dir_is_for_a_burn(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws, fonts_dir="fonts", mode="mux", output_path="out.mkv"))
    assert exc.value.code == "INVALID_INPUT"
    assert "burn" in exc.value.message


def test_mux_operation_does_not_take_fonts_dir(tmp_path, fake_engine):
    ws = _workspace(tmp_path)
    request = {"operation": "mux", "workspace": str(ws), "video_input": "in.mp4", "output_path": "out.mkv",
               "tracks": [{"subtitle": _request(ws)["subtitle"]}], "fonts_dir": "fonts"}
    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "INVALID_REQUEST"


def test_a_caption_tool_without_the_flags_is_a_dependency_error(tmp_path, monkeypatch):
    # the repo's pattern for an optional engine capability (mux: caption_supports_multitrack)
    _install_fake(tmp_path, monkeypatch, flags=())
    ws = _workspace(tmp_path)
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws, fonts_dir="fonts"))
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert "fonts_dir" in exc.value.message and "ffmpeg-skill" in exc.value.message
    assert not (ws / "out.mp4").exists()
    # the same tool still burns a request that does not ask for it
    assert execute(_request(ws))["status"] == "ok"


def test_burn_in_refuses_a_caption_tool_without_the_flags(tmp_path, monkeypatch):
    import subtitle_skill.engine as engine

    _install_fake(tmp_path, monkeypatch, flags=('"--fonts-dir"',))
    ws = _workspace(tmp_path)
    (ws / "cues.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n", encoding="utf-8")
    kwargs = dict(video_path=ws / "in.mp4", subtitle_path=ws / "cues.srt", subtitle_format="srt",
                  output_path=ws / "out.mp4")
    with pytest.raises(SubtitleSkillError) as exc:
        engine.burn_in(fonts_dir=ws, font_family="Example Gothic", **kwargs)
    assert exc.value.code == "DEPENDENCY_ERROR"
    for bad in (dict(fonts_dir=ws), dict(font_family="Example Gothic"),
                dict(fonts_dir=ws, font_family="Example Gothic", mode="mux")):
        with pytest.raises(SubtitleSkillError) as exc:
            engine.burn_in(**kwargs, **bad)
        assert exc.value.code == "INVALID_INPUT"


def test_engine_font_refusal_names_the_field_and_keeps_the_engines_reason(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, refusal=ENGINE_FONT_REFUSAL)
    ws = _workspace(tmp_path)

    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws))

    error = exc.value
    assert error.code == "INVALID_INPUT" and error.retryable is False
    assert ENGINE_FONT_REFUSAL in error.message  # the engine's own reason, whole
    assert "request.fonts_dir" in error.message
    assert error.message.index(ENGINE_FONT_REFUSAL) < error.message.index("request.fonts_dir")
    assert error.details == {"field": "fonts_dir"}
    assert error.to_dict()["details"] == {"field": "fonts_dir"}
    # a consumer that keeps the first 500 characters of a message still reads the field
    assert "request.fonts_dir" in error.message[:500]

    # the way out the message names works on the same machine
    _write_font(ws / "fonts" / "ja.ttf", "Example Gothic", JA_RANGES)
    assert execute(_request(ws, fonts_dir="fonts"))["status"] == "ok"


def test_another_engine_refusal_is_left_as_it_is(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, refusal="every cue is blank -- nothing would be drawn")
    ws = _workspace(tmp_path)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(ws))
    assert exc.value.code == "INVALID_INPUT"
    assert "fonts_dir" not in exc.value.message
    assert exc.value.details is None


def test_contract_names_the_field():
    from subtitle_skill.contract import build_contract

    contract = build_contract()
    render = contract["operations"]["render"]
    assert "fonts_dir?" in render["inputs"] and "fonts_dir?" in render["outputs"]
    assert "fonts_dir" in render["description"]
    assert "fonts_dir" in contract["parameters"]["render"]
    assert contract["capabilities"]["fonts_dir"] is True
    assert "fonts_dir" not in contract["parameters"]["mux"]


def test_the_located_vendored_caption_tool_takes_a_fonts_directory():
    import subtitle_skill.engine as engine

    assert engine.caption_takes_fonts_dir(VENDOR_ROOT)
    source = (VENDOR_ROOT / "scripts" / "_common" / "fonts.py").read_text(encoding="utf-8")
    # the sentence the guided error is matched on is still how the tool refuses
    assert engine.FONT_REFUSAL_MARKER in source


# --- render against the real caption tool and a real font ----------------------

_REQUIRE_REAL = os.environ.get("SUBTITLE_SKILL_REQUIRE_REAL_ENGINE") == "1"


def _unavailable(reason: str):
    if _REQUIRE_REAL:
        pytest.fail(f"SUBTITLE_SKILL_REQUIRE_REAL_ENGINE=1 but {reason}")
    pytest.skip(reason)


def _fc_files(pattern: str) -> list:
    proc = subprocess.run(["fc-list", pattern, "file"], capture_output=True, text=True, check=True)
    return sorted({line.split(":")[0].strip() for line in proc.stdout.splitlines() if line.strip()})


@pytest.fixture()
def machine_without_a_japanese_font(tmp_path, monkeypatch):
    """A real ffmpeg-skill install, a copy of one installed Japanese font file
    (returned, outside every font path), and a fontconfig -- the one ffmpeg-skill
    asks and the one libass draws through -- that knows a single Latin font and
    no Japanese one. This is the venue PC: the font exists only as a file."""
    if not sys.platform.startswith("linux"):
        _unavailable("the fontconfig sandbox for this test is set up on Linux only")
    if not (shutil.which("ffmpeg") and shutil.which("fc-list")):
        _unavailable("real ffmpeg and fontconfig (fc-list) are required")
    japanese = [f for f in _fc_files(":lang=ja") if f.lower().endswith(fonts.FONT_SUFFIXES)]
    latin = [f for f in _fc_files(":lang=en") if f not in set(_fc_files(":lang=ja"))
             and f.lower().endswith((".ttf", ".otf"))]
    if not japanese or not latin:
        _unavailable("an installed Japanese font (e.g. fonts-noto-cjk) and a Latin-only font are needed to copy from")
    # a regular weight when there is one, so the frames are drawn as a caption usually is
    source = next((f for f in japanese if "regular" in Path(f).name.lower()), japanese[0])

    sandbox = tmp_path / "fontconfig"
    (sandbox / "fonts").mkdir(parents=True)
    (sandbox / "cache").mkdir()
    shutil.copy(latin[0], sandbox / "fonts")
    conf = sandbox / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        f"  <dir>{sandbox / 'fonts'}</dir>\n  <cachedir>{sandbox / 'cache'}</cachedir>\n</fontconfig>\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FONTCONFIG_FILE", str(conf))
    assert _fc_files(":lang=en"), "the sandboxed fontconfig should still know its Latin font"
    assert _fc_files(":lang=ja") == [], "the sandboxed fontconfig must not know any Japanese font"

    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return Path(source)


def _real_workspace(tmp_path, font_source: Path = None) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(ws / "in.mp4")],
        check=True, capture_output=True,
    )
    if font_source is not None:
        (ws / "fonts").mkdir()
        shutil.copy(font_source, ws / "fonts")
    return ws


def _frame(path: Path) -> bytes:
    """The middle frame as 640x360 grey bytes."""
    proc = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-ss", "0.5", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    )
    assert len(proc.stdout) == 640 * 360
    return proc.stdout


def _ja_request(ws: Path, text: str, output: str, **extra) -> dict:
    request = _request(ws, output_path=output, **extra)
    request["subtitle"]["cues"] = [{"id": "c1", "start": 0, "end": 1, "text": text}]
    return request


#: Two captions of the same length with no character in common. A font that
#: has neither draws the same row of empty boxes for both.
_JA_A = "\u5b57\u5e55\u306e\u30c6\u30b9\u30c8"  # 字幕のテスト
_JA_B = "\u65e5\u672c\u8a9e\u3092\u713c\u304f"  # 日本語を焼く


def test_real_engine_refuses_japanese_without_a_font_and_says_which_field(tmp_path, machine_without_a_japanese_font):
    ws = _real_workspace(tmp_path)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_ja_request(ws, _JA_A, "out.mp4"))
    error = exc.value
    assert error.code == "INVALID_INPUT"
    assert "no installed font covers Japanese text" in error.message
    assert "request.fonts_dir" in error.message
    assert not (ws / "out.mp4").exists()


@pytest.mark.parametrize("through_ass", [False, True], ids=["srt-burn", "ass-burn"])
def test_real_engine_burns_japanese_from_fonts_dir(tmp_path, machine_without_a_japanese_font, through_ass):
    ws = _real_workspace(tmp_path, machine_without_a_japanese_font)
    # a caller-named region away from the caption sends the burn through subtitle-skill's ASS
    extra = {"placement": {"avoid": [{"name": "logo", "box": [0.0, 0.0, 0.1, 0.1]}]}} if through_ass else {}

    blank = _frame(ws / "in.mp4")
    frames = {}
    for name, text in (("a", _JA_A), ("b", _JA_B)):
        response = execute(_ja_request(ws, text, f"{name}.mp4", fonts_dir="fonts", **extra))
        assert response["status"] == "ok" and response["reused"] is False
        assert response["fonts_dir"]["path"] == "fonts"
        assert response["fonts_dir"]["files"] == [machine_without_a_japanese_font.name]
        assert [o for o in response["observation"] if o["code"].startswith("FONT_")] == []
        engine_response = json.loads(
            (ws / f"{name}.mp4.subtitle-skill.json").read_text(encoding="utf-8")
        )["engine_response"]
        command = " ".join(engine_response["commands"])
        assert "fontsdir=" in command
        assert ("ass=" in command) is through_ass
        frames[name] = _frame(ws / f"{name}.mp4")

    # something was drawn, and the two captions are not the same picture: glyphs, not empty boxes
    assert frames["a"] != blank and frames["b"] != blank
    assert frames["a"] != frames["b"]
    family = response["fonts_dir"]["family"]
    assert family in {f.family for f in fonts.scan(ws / "fonts")}
