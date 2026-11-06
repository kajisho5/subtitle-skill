"""Roadmap step 9: TTML (IMSC 1.1 Text Profile) and EBU-TT-D output (docs/adr/ADR-007-ttml-output.md)."""
from pathlib import Path

import pytest

from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.formats import GENERATORS, generate_srt, generate_vtt, parse_srt, parse_ttml, validate_ttml
from subtitle_skill.formats.ttml import decode_id, encode_id
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.operations import execute

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = FIXTURES / "schemas" / "ttml2" / "ttml2.xsd"
PROFILES = ("ttml", "ebu-tt-d")


def _xsd():
    etree = pytest.importorskip("lxml.etree")
    return etree, etree.XMLSchema(etree.parse(str(SCHEMA)))


@pytest.mark.parametrize("language", ["ja", "en"])
@pytest.mark.parametrize("profile", PROFILES)
def test_srt_round_trips_through_ttml_with_the_same_cues(language, profile):
    srt = (FIXTURES / "ttml" / f"{language}.srt").read_text(encoding="utf-8")
    document = parse_srt(srt, language=language)
    ttml = GENERATORS[profile](document)
    assert validate_ttml(ttml, profile=profile) == []
    back = parse_ttml(ttml)
    assert back.language == language
    assert [c.to_dict() for c in back.cues] == [c.to_dict() for c in document.cues]
    assert generate_srt(back) == srt


@pytest.mark.parametrize("language", ["ja", "en"])
@pytest.mark.parametrize("profile", PROFILES)
def test_ttml_validates_against_the_w3c_ttml2_schema(language, profile):
    etree, schema = _xsd()
    document = parse_srt((FIXTURES / "ttml" / f"{language}.srt").read_text(encoding="utf-8"), language=language)
    tree = etree.fromstring(GENERATORS[profile](document).encode("utf-8"))
    assert schema.validate(tree), [str(e) for e in schema.error_log]


def _positioned():
    return SubtitleDocument.from_dict({
        "id": "pos", "language": "en",
        "cues": [
            {"id": "a", "start": 0, "end": 1, "text": "bottom"},
            {"id": "b", "start": 1, "end": 2, "text": "top left", "style": {"line": 10, "align": "left"}},
            {"id": "c", "start": 2, "end": 3, "text": "narrow", "style": {"position": 30, "size": 40}},
            {"id": "d", "start": 3, "end": 4, "text": "red", "style": {"color": "#FF0000", "bold": True}},
        ],
    })


@pytest.mark.parametrize("profile", PROFILES)
def test_webvtt_positions_are_kept_as_regions(profile):
    document = _positioned()
    ttml = GENERATORS[profile](document)
    assert 'tts:origin="10% 10%" tts:extent="80% 90%" tts:displayAlign="before"' in ttml
    assert 'tts:origin="10% 10%" tts:extent="40% 80%"' in ttml
    back = parse_ttml(ttml)
    styles = {c.id: (c.style.to_dict() if c.style else {}) for c in back.cues}
    assert styles["a"] == {}
    assert styles["b"] == {"line": 10.0, "align": "left"}
    assert styles["c"] == {"position": 30.0, "size": 40.0}
    assert styles["d"] == {"color": "FF0000", "bold": True}
    # the WebVTT of the positioned cues is the same before and after the TTML round trip
    without_color = SubtitleDocument.from_dict({**document.to_dict(), "cues": [c.to_dict() for c in document.cues[:3]]})
    assert generate_vtt(SubtitleDocument.from_dict({**back.to_dict(), "cues": [c.to_dict() for c in back.cues[:3]]})) == \
        generate_vtt(without_color)


@pytest.mark.parametrize("profile", PROFILES)
def test_positioned_document_validates_against_the_schema(profile):
    etree, schema = _xsd()
    tree = etree.fromstring(GENERATORS[profile](_positioned()).encode("utf-8"))
    assert schema.validate(tree), [str(e) for e in schema.error_log]


def test_profiles_differ_where_the_standards_do():
    document = _positioned()
    imsc = GENERATORS["ttml"](document)
    ebu = GENERATORS["ebu-tt-d"](document)
    assert 'ttp:contentProfiles="http://www.w3.org/ns/ttml/profile/imsc1.1/text"' in imsc
    assert "urn:ebu:tt:distribution:2018-04" in ebu and "contentProfiles" not in ebu
    # EBU-TT-D: styling by reference only
    assert "<span style=" in ebu and "<span tts:" not in ebu


def test_cue_ids_become_ncnames_reversibly():
    for cue_id in ("1", "c 2", "a_b", "字幕", "x.y-z"):
        xml_id = encode_id(cue_id)
        assert xml_id.startswith("cue-") and " " not in xml_id
        assert decode_id(xml_id) == cue_id


@pytest.mark.parametrize(
    "mutate, problem",
    [
        (lambda t: t.replace('ttp:timeBase="media"', ""), "timeBase"),
        (lambda t: t.replace('region="bottom"', 'region="nowhere"'), "not defined"),
        (lambda t: t.replace('end="00:00:01.000"', 'end="00:00:00.000"'), "end is not after begin"),
        (lambda t: t.replace('tts:extent="80% 80%"', 'tts:extent="95% 80%"'), "outside the root container"),
        (lambda t: t.replace("<span>", '<span tts:color="#ff0000">'), "inline"),
        (lambda t: t.replace("urn:ebu:tt:distribution:2018-04", "x"), "does not declare"),
    ],
)
def test_validator_finds_profile_violations(mutate, problem):
    ebu = GENERATORS["ebu-tt-d"](_positioned())
    problems = validate_ttml(mutate(ebu), profile="ebu-tt-d")
    assert any(problem in p for p in problems), problems


def test_imsc_validator_needs_the_profile_designator():
    imsc = GENERATORS["ttml"](_positioned())
    assert validate_ttml(imsc, profile="ttml") == []
    assert validate_ttml(imsc.replace("imsc1.1/text", "imsc1/text"), profile="ttml")


@pytest.mark.parametrize("profile", PROFILES)
def test_generate_writes_ttml_and_reports_conformance(tmp_path, profile):
    response = execute({
        "operation": "generate", "workspace": str(tmp_path), "format": profile, "output_path": "out.xml",
        "subtitle": parse_srt((FIXTURES / "ttml" / "ja.srt").read_text(encoding="utf-8"), language="ja").to_dict(),
    })
    assert response["status"] == "ok"
    assert response["conformance"]["valid"] is True
    written = (tmp_path / "out.xml").read_text(encoding="utf-8")
    assert validate_ttml(written, profile=profile) == []
    assert execute({
        "operation": "generate", "workspace": str(tmp_path), "format": profile, "output_path": "out.xml",
        "subtitle": parse_srt((FIXTURES / "ttml" / "ja.srt").read_text(encoding="utf-8"), language="ja").to_dict(),
    })["conformance"]["valid"] is True


def test_translate_writes_ttml(tmp_path):
    response = execute({
        "operation": "translate", "workspace": str(tmp_path), "format": "ebu-tt-d", "output_path": "en.ttml",
        "target_language": "en", "engine": "memory",
        "translation_memory": [{"source": "こんにちは。", "target": "Hello."}],
        "subtitle": {"id": "d", "language": "ja", "cues": [{"id": "1", "start": 0, "end": 2, "text": "こんにちは。"}]},
    })
    assert response["conformance"]["profile"] == "urn:ebu:tt:distribution:2018-04"
    back = parse_ttml((tmp_path / "en.ttml").read_text(encoding="utf-8"))
    assert back.language == "en" and back.cues[0].text == "Hello."


def test_render_still_refuses_ttml(tmp_path):
    with pytest.raises(SubtitleSkillError) as exc:
        execute({
            "operation": "render", "workspace": str(tmp_path), "format": "ttml", "output_path": "o.mp4",
            "video_input": "in.mp4", "subtitle": {"id": "d", "language": "en", "cues": [
                {"id": "1", "start": 0, "end": 1, "text": "x"}]},
        })
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_parse_srt_rejects_a_block_without_timing():
    with pytest.raises(SubtitleSkillError):
        parse_srt("1\nno timing here\n", language="en")
