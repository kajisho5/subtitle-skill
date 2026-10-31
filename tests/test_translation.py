"""`translate` (ECOSYSTEM_ROADMAP step 5, docs/adr/ADR-004-translation-engine.md).

No test here downloads a model or reaches the network: `memory` needs
neither, Argos is replaced by a fake `argostranslate` package in
`sys.modules`, and DeepL by a fake HTTP function.
"""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from subtitle_skill import translation
from subtitle_skill.errors import SubtitleSkillError
from subtitle_skill.locales import PROFILES
from subtitle_skill.operations import execute

FIXTURES = Path(__file__).parent / "fixtures" / "translation"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request(tmp_path, fx, **over):
    req = {
        "operation": "translate",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.srt",
        "engine": "memory",
        "target_language": fx["expected"]["language"],
        "glossary": fx["glossary"],
        "translation_memory": fx["translation_memory"],
        "subtitle": fx["source"],
    }
    req.update(over)
    return req


# --------------------------------------------------------------------------
# ja <-> en acceptance fixtures (roadmap step 5 "Done when")
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ja_en.json", "en_ja.json"])
def test_fixture_translation_keeps_timing_and_lays_out_for_the_target(tmp_path, name):
    fx = _fixture(name)
    resp = execute(_request(tmp_path, fx))
    assert resp["status"] == "ok"
    assert resp["operation"] == "translate"
    doc = resp["subtitle"]
    assert doc["language"] == fx["expected"]["language"]
    assert len(doc["cues"]) == len(fx["expected"]["cues"])
    for got, want, src in zip(doc["cues"], fx["expected"]["cues"], fx["source"]["cues"]):
        assert got["id"] == want["id"] == src["id"]
        assert got["start"] == want["start"] == src["start"]
        assert got["end"] == want["end"] == src["end"]
        assert got["text"] == want["text"]
        assert got.get("speaker") == want.get("speaker")
        assert got["metadata"]["translation"]["source_text"] == src["text"]
    observed = [(o["code"], o.get("cue_id")) for o in resp["observation"]]
    assert observed == [(o["code"], o["cue_id"]) for o in fx["expected"]["observations"]]


def test_ja_target_lines_respect_the_ja_profile(tmp_path):
    fx = _fixture("en_ja.json")
    resp = execute(_request(tmp_path, fx))
    ja = PROFILES["ja"]
    for cue in resp["subtitle"]["cues"]:
        lines = cue["text"].split("\n")
        assert all(ja.text_width(line) <= ja.max_chars_per_line for line in lines)
        assert all(line[0] not in ja.no_line_start for line in lines[1:])
        assert all(line[-1] not in ja.no_line_end for line in lines[:-1])


def test_output_file_is_the_translated_document(tmp_path):
    fx = _fixture("ja_en.json")
    resp = execute(_request(tmp_path, fx, format="vtt", output_path="out.en.vtt"))
    text = Path(resp["output"]).read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "00:00:00.500 --> 00:00:03.000" in text
    assert "Today we will try translating subtitles." in text


def test_provenance_records_the_engine(tmp_path):
    fx = _fixture("ja_en.json")
    resp = execute(_request(tmp_path, fx))
    assert resp["engine"] == "memory"
    assert resp["engine_version"].startswith("memory:")
    meta = resp["subtitle"]["metadata"]["translation"]
    assert meta["engine"] == "memory"
    assert meta["source_language"] == "ja" and meta["target_language"] == "en"
    assert meta["source_document_id"] == "ep01"
    assert len(meta["glossary_sha256"]) == 64
    sidecar = json.loads(Path(resp["output"] + ".subtitle-skill.json").read_text(encoding="utf-8"))
    assert sidecar["engine"] == "memory"
    assert sidecar["engine_version"] == resp["engine_version"]
    assert sidecar["document"] == resp["subtitle"]


def test_repeat_request_is_reused_and_returns_the_document(tmp_path):
    fx = _fixture("ja_en.json")
    first = execute(_request(tmp_path, fx))
    second = execute(_request(tmp_path, fx))
    assert first["reused"] is False and second["reused"] is True
    assert second["subtitle"] == first["subtitle"]
    assert second["sha256"] == first["sha256"]
    assert [o["code"] for o in second["observation"]] == [o["code"] for o in first["observation"]]
    # a different glossary is a different request
    third = execute(_request(tmp_path, fx, glossary=[{"source": "字幕", "target": "captions"}]))
    assert third["reused"] is False


def test_target_constraints_override_profile_defaults(tmp_path):
    fx = _fixture("ja_en.json")
    resp = execute(_request(tmp_path, fx, constraints={"max_chars_per_line": 20, "reading_speed_cps": 100}))
    for cue in resp["subtitle"]["cues"]:
        assert all(len(line) <= 20 for line in cue["text"].split("\n"))
    assert "READING_SPEED_TOO_HIGH" not in [o["code"] for o in resp["observation"]]


def test_cli_translate_round_trip(tmp_path):
    fx = _fixture("en_ja.json")
    proc = subprocess.run(
        [sys.executable, "-m", "subtitle_skill", "run", "-", "--json"],
        input=json.dumps(_request(tmp_path, fx)),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["subtitle"]["cues"][0]["text"] == fx["expected"]["cues"][0]["text"]


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over,code",
    [
        ({"target_language": "ja"}, "INVALID_INPUT"),  # same as the source
        ({"target_language": "e n"}, "INVALID_INPUT"),
        ({"target_language": None}, "MISSING_INPUT"),
        ({"engine": "google"}, "INVALID_INPUT"),
        ({"engine": "deepl"}, "INVALID_INPUT"),  # translation_memory only for memory
        ({"translation_memory": []}, "MISSING_INPUT"),
        ({"glossary": [{"source": "a"}]}, "INVALID_INPUT"),
        ({"glossary": [{"source": "a", "target": "b"}, {"source": "a", "target": "c"}]}, "INVALID_INPUT"),
        ({"format": "ass"}, "UNSUPPORTED_FORMAT"),
        ({"surprise": 1}, "INVALID_REQUEST"),
    ],
)
def test_invalid_translate_requests(tmp_path, over, code):
    fx = _fixture("ja_en.json")
    req = _request(tmp_path, fx, **over)
    if over.get("target_language", "x") is None:
        del req["target_language"]
    with pytest.raises(SubtitleSkillError) as exc:
        execute(req)
    assert exc.value.code == code


def test_api_key_in_a_request_is_refused(tmp_path):
    fx = _fixture("ja_en.json")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, fx, engine="deepl", translation_memory=None, deepl={"api_key": "x"}))
    assert exc.value.code == "INVALID_REQUEST"
    assert "api_key" in exc.value.message


def test_memory_without_an_entry_names_the_segment(tmp_path):
    fx = _fixture("ja_en.json")
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_request(tmp_path, fx, translation_memory=fx["translation_memory"][:1]))
    assert exc.value.code == "MISSING_INPUT"
    assert "よろしく。" in exc.value.details["missing_segments"]


# --------------------------------------------------------------------------
# Layout and glossary helpers
# --------------------------------------------------------------------------


def test_layout_keeps_short_text_on_one_line():
    assert translation.layout_text("Hello there.", None, 42, 2) == "Hello there."


def test_layout_wraps_past_two_lines_when_it_must():
    text = "one two three four five six seven eight nine ten eleven twelve"
    out = translation.layout_text(text, None, 15, 2)
    assert all(len(line) <= 15 for line in out.split("\n"))
    assert len(out.split("\n")) > 2  # validation then reports TOO_MANY_LINES


def test_layout_never_breaks_before_a_small_kana():
    ja = PROFILES["ja"]
    out = translation.layout_text("ちょっとまってくださいねっていったでしょう", ja, 13, 2)
    for line in out.split("\n")[1:]:
        assert line[0] not in ja.no_line_start


def test_glossary_substitution_longest_first():
    glossary = translation.parse_glossary(
        [{"source": "字幕", "target": "subtitle"}, {"source": "字幕翻訳", "target": "subtitle translation"}]
    )
    assert translation.apply_glossary_to_source("字幕翻訳と字幕", glossary) == "subtitle translationとsubtitle"


def test_glossary_check_is_case_insensitive_on_the_output():
    glossary = translation.parse_glossary([{"source": "字幕", "target": "Subtitles"}])
    assert translation.glossary_misses("字幕です", "subtitles here", glossary) == []
    assert translation.glossary_misses("字幕です", "captions here", glossary) == list(glossary)


# --------------------------------------------------------------------------
# Argos (fake module; nothing is installed or downloaded)
# --------------------------------------------------------------------------


class _FakeArgos:
    def __init__(self, packages=(("ja", "en"),), provider="opennmt"):
        self.calls = []
        self.downloads = []
        fake = self

        class Pkg:
            def __init__(self, src, tgt):
                self.from_code, self.to_code, self.package_version = src, tgt, "1.1"

        class Translation:
            def translate(self, text):
                fake.calls.append(text)
                return text.replace("を翻訳します。", " get translated.")

        def _forbidden(*_a, **_k):
            fake.downloads.append(True)
            raise AssertionError("subtitle-skill must never download Argos packages")

        self.package = types.SimpleNamespace(
            get_installed_packages=lambda: [Pkg(s, t) for s, t in packages],
            update_package_index=_forbidden,
            install_package_for_language_pair=_forbidden,
            get_available_packages=_forbidden,
        )
        self.translate = types.SimpleNamespace(get_translation_from_codes=lambda s, t: Translation())

        class ModelProvider:
            OPENNMT = "opennmt"
            LIBRETRANSLATE = "libretranslate"

        self.settings = types.SimpleNamespace(ModelProvider=ModelProvider, model_provider=provider)

    def install(self, monkeypatch):
        root = types.ModuleType("argostranslate")
        root.package, root.translate, root.settings = self.package, self.translate, self.settings
        monkeypatch.setitem(sys.modules, "argostranslate", root)
        monkeypatch.setitem(sys.modules, "argostranslate.package", self.package)
        monkeypatch.setitem(sys.modules, "argostranslate.translate", self.translate)
        monkeypatch.setitem(sys.modules, "argostranslate.settings", self.settings)


def _argos_request(tmp_path, **over):
    req = {
        "operation": "translate",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.srt",
        "target_language": "en",
        "glossary": [{"source": "字幕", "target": "captions"}],
        "subtitle": {"id": "d", "language": "ja", "cues": [{"id": "1", "start": 0, "end": 3, "text": "字幕を\n翻訳します。"}]},
    }
    req.update(over)
    return req


def test_argos_is_the_default_engine_and_gets_glossary_terms_substituted(tmp_path, monkeypatch):
    fake = _FakeArgos()
    fake.install(monkeypatch)
    resp = execute(_argos_request(tmp_path))
    assert resp["engine"] == "argos"
    assert "package ja_en 1.1" in resp["engine_version"]
    # the engine saw one joined segment, with the glossary target already in place
    assert fake.calls == ["captionsを翻訳します。"]
    assert fake.downloads == []
    assert "GLOSSARY_TERM_MISSING" not in [o["code"] for o in resp["observation"]]


def test_argos_output_missing_a_glossary_term_is_reported(tmp_path, monkeypatch):
    fake = _FakeArgos()
    fake.install(monkeypatch)
    fake.translate.get_translation_from_codes = lambda s, t: types.SimpleNamespace(translate=lambda text: "Translated.")
    resp = execute(_argos_request(tmp_path))
    assert [o["code"] for o in resp["observation"]] == ["GLOSSARY_TERM_MISSING"]


def test_argos_not_installed_is_a_dependency_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "argostranslate", None)
    monkeypatch.setitem(sys.modules, "argostranslate.package", None)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_argos_request(tmp_path))
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert "never downloads" in exc.value.message


def test_argos_missing_language_package_is_a_dependency_error(tmp_path, monkeypatch):
    fake = _FakeArgos(packages=(("en", "ja"),))
    fake.install(monkeypatch)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_argos_request(tmp_path))
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert "ja->en" in exc.value.message
    assert fake.downloads == []


def test_argos_with_a_remote_provider_is_refused(tmp_path, monkeypatch):
    fake = _FakeArgos(provider="libretranslate")
    fake.install(monkeypatch)
    with pytest.raises(SubtitleSkillError) as exc:
        execute(_argos_request(tmp_path))
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert "offline only" in exc.value.message
    assert fake.calls == []


# --------------------------------------------------------------------------
# DeepL (fake HTTP; CI never calls the API)
# --------------------------------------------------------------------------


class _FakeDeepL:
    def __init__(self, translate_status=200):
        self.requests = []
        self.translate_status = translate_status

    def __call__(self, method, url, headers, body):
        payload = json.loads(body.decode("utf-8")) if body else None
        self.requests.append((method, url, dict(headers), payload))
        if url.endswith("/v2/glossaries") and method == "POST":
            return 201, json.dumps({"glossary_id": "g-123", "ready": True}).encode()
        if "/v2/glossaries/" in url and method == "DELETE":
            return 204, b""
        if url.endswith("/v2/translate"):
            if self.translate_status != 200:
                return self.translate_status, b'{"message":"nope"}'
            texts = payload["text"]
            return 200, json.dumps({"translations": [{"text": "captions: " + t} for t in texts]}).encode()
        return 404, b""


def _deepl_engine(fake, key="secret:fx"):
    return translation.DeepLEngine(http=fake, environ={translation.DEEPL_KEY_ENV: key})


def test_deepl_uses_a_native_glossary_and_cleans_it_up():
    fake = _FakeDeepL()
    engine = _deepl_engine(fake)
    glossary = translation.parse_glossary([{"source": "字幕", "target": "captions"}])
    out = engine.translate(["字幕です"], "ja", "en", glossary)
    assert out == ["captions: 字幕です"]  # the source is NOT pre-substituted for DeepL
    methods = [(m, u.rsplit("/v2", 1)[1]) for m, u, _h, _p in fake.requests]
    assert methods == [("POST", "/glossaries"), ("POST", "/translate"), ("DELETE", "/glossaries/g-123")]
    created = fake.requests[0][3]
    assert created["entries"] == "字幕\tcaptions" and created["entries_format"] == "tsv"
    assert created["source_lang"] == "ja" and created["target_lang"] == "en"
    sent = fake.requests[1][3]
    assert sent["glossary_id"] == "g-123"
    assert sent["source_lang"] == "JA" and sent["target_lang"] == "EN-US"
    headers = fake.requests[1][2]
    assert headers["Authorization"] == "DeepL-Auth-Key secret:fx"
    assert fake.requests[1][1].startswith(translation.DEEPL_FREE_URL)


def test_deepl_pro_key_uses_the_pro_url_and_regional_targets():
    fake = _FakeDeepL()
    engine = _deepl_engine(fake, key="secret")
    engine.translate(["hello"], "en", "ja", ())
    assert fake.requests[0][1] == translation.DEEPL_PRO_URL + "/v2/translate"
    assert fake.requests[0][3]["target_lang"] == "JA"
    assert translation.deepl_target_code("en-GB") == "EN-GB"
    assert translation.deepl_target_code("pt") == "PT-BR"
    assert translation.deepl_target_code("zh-Hant") == "ZH-HANT"


def test_deepl_without_a_key_is_a_dependency_error():
    engine = translation.DeepLEngine(http=_FakeDeepL(), environ={})
    with pytest.raises(SubtitleSkillError) as exc:
        engine.translate(["x"], "ja", "en", ())
    assert exc.value.code == "DEPENDENCY_ERROR"
    assert translation.DEEPL_KEY_ENV in exc.value.message


def test_deepl_quota_and_glossary_cleanup_on_failure():
    fake = _FakeDeepL(translate_status=456)
    engine = _deepl_engine(fake)
    glossary = translation.parse_glossary([{"source": "a", "target": "b"}])
    with pytest.raises(SubtitleSkillError) as exc:
        engine.translate(["a"], "en", "ja", glossary)
    assert exc.value.code == "DEPENDENCY_ERROR" and "456" in exc.value.message
    assert fake.requests[-1][0] == "DELETE"


def test_deepl_count_mismatch_is_a_tool_error():
    def http(method, url, headers, body):
        return 200, json.dumps({"translations": []}).encode()

    engine = translation.DeepLEngine(http=http, environ={translation.DEEPL_KEY_ENV: "k"})
    with pytest.raises(SubtitleSkillError) as exc:
        engine.translate(["a", "b"], "en", "ja", ())
    assert exc.value.code == "TOOL_ERROR"


def test_deepl_through_execute_with_the_http_layer_replaced(tmp_path, monkeypatch):
    fake = _FakeDeepL()
    monkeypatch.setenv(translation.DEEPL_KEY_ENV, "secret:fx")
    monkeypatch.setattr(translation, "_urllib_http", fake)
    req = _argos_request(tmp_path, engine="deepl")
    resp = execute(req)
    assert resp["engine"] == "deepl"
    assert resp["engine_version"] == "deepl-api v2"
    assert resp["subtitle"]["cues"][0]["text"].startswith("captions:")
    assert resp["subtitle"]["cues"][0]["start"] == 0 and resp["subtitle"]["cues"][0]["end"] == 3


def test_doctor_lists_translation_engines(monkeypatch):
    from subtitle_skill.doctor import build_doctor_report

    monkeypatch.delenv(translation.DEEPL_KEY_ENV, raising=False)
    report = build_doctor_report()
    assert "translate" in report["supported_operations"]
    engines = report["translation_engines"]
    assert engines["memory"]["available"] is True
    assert engines["deepl"]["available"] is False and engines["deepl"]["offline"] is False
    assert engines["argos"]["offline"] is True
