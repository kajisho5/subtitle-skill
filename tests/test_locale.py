"""Locale profiles (ja/zh/ko): width counting, kinsoku, per-locale defaults.

The Japanese fixtures under tests/fixtures/locale/ are the acceptance test
for ECOSYSTEM_ROADMAP.md step 3. Sources for every default are recorded in
docs/adr/ADR-001-locale-profiles.md.
"""
import json
from pathlib import Path

import pytest

from subtitle_skill.locales import PROFILES, break_lines, kinsoku_violations, profile_for
from subtitle_skill.models import SubtitleDocument
from subtitle_skill.operations import execute
from subtitle_skill.validation import (
    DEFAULT_MAX_CHARS_PER_LINE,
    DEFAULT_READING_SPEED_CPS,
    SubtitleConstraints,
    validate_document,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "locale"
BREAKING = json.loads((FIXTURES / "ja_line_breaking.json").read_text(encoding="utf-8"))
VALIDATION = json.loads((FIXTURES / "ja_validation.json").read_text(encoding="utf-8"))

JA = PROFILES["ja"]


def _doc(cues, language="ja"):
    return SubtitleDocument.from_dict({"id": "d", "language": language, "cues": cues})


# ---- Japanese fixtures ------------------------------------------------------


@pytest.mark.parametrize("case", BREAKING["cases"], ids=[c["name"] for c in BREAKING["cases"]])
def test_ja_fixture_line_breaking(case):
    lines = break_lines(case["text"], JA, BREAKING["max_width"])
    assert lines == case["expected"]
    assert all(JA.text_width(line) <= BREAKING["max_width"] for line in lines)
    assert kinsoku_violations(lines, JA) == []


@pytest.mark.parametrize("case", VALIDATION["cases"], ids=[c["name"] for c in VALIDATION["cases"]])
def test_ja_fixture_validation(case):
    issues = validate_document(_doc(case["cues"], case["language"]))
    assert sorted([i.code, i.cue_id] for i in issues) == sorted(case["expected"])
    if "suggested_text" in case:
        for issue in issues:
            assert issue.suggested_text == case["suggested_text"]


# ---- counting ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,width",
    [
        ("あいう", 3),
        ("ABC", 1.5),  # Na
        ("ｱｲｳ", 1.5),  # H: halfwidth katakana
        ("ＡＢＣ", 3),  # F: fullwidth Latin
        ("…", 1),  # A: ambiguous resolves to wide in an East Asian context (UAX #11 section 5)
        ("が", 1),  # NFC
        ("が", 1),  # NFD: the combining voiced mark adds nothing
        ("　", 1),  # ideographic space
        (" ", 0.5),
        ("あ\nい", 2),  # line breaks are not characters
    ],
)
def test_ja_width_follows_east_asian_width(text, width):
    assert JA.text_width(text) == width


def test_zh_counts_half_width_as_one_and_ko_as_half():
    assert PROFILES["zh"].text_width("AB中文") == 4
    assert PROFILES["ko"].text_width("AB 한국") == 3.5


# ---- profile selection and defaults -----------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [("ja", "ja"), ("ja-JP", "ja"), ("JA", "ja"), ("zh-Hans", "zh"), ("zh-TW", "zh"), ("ko-KR", "ko"),
     ("en", None), ("en-US", None), ("fr", None), ("jav", None)],
)
def test_profile_selected_by_primary_subtag(tag, expected):
    profile = profile_for(tag)
    assert (profile.language if profile else None) == expected


def test_profile_defaults_are_the_cited_values():
    # Netflix Timed Text Style Guides (Japanese / Chinese (Simplified, Traditional) / Korean) -- see ADR-001
    assert (JA.max_chars_per_line, JA.reading_speed_cps, JA.half_width_weight) == (13, 4.0, 0.5)
    zh = PROFILES["zh"]
    assert (zh.max_chars_per_line, zh.reading_speed_cps, zh.half_width_weight) == (16, 9.0, 1.0)
    ko = PROFILES["ko"]
    assert (ko.max_chars_per_line, ko.reading_speed_cps, ko.half_width_weight) == (16, 12.0, 0.5)


def test_caller_constraints_override_profile_defaults():
    doc = _doc([{"id": "c1", "start": 0, "end": 1, "text": "今日はとても良い天気ですね。"}])
    assert {i.code for i in validate_document(doc)} == {"LINE_TOO_LONG", "READING_SPEED_TOO_HIGH"}
    loose = SubtitleConstraints.from_dict({"max_chars_per_line": 20, "reading_speed_cps": 20})
    assert validate_document(doc, constraints=loose) == []


def test_zh_and_ko_defaults_apply():
    zh = _doc([{"id": "c1", "start": 0, "end": 10, "text": "这是一个非常非常长的中文字幕句子啊"}], "zh-Hans")
    assert [i.code for i in validate_document(zh)] == ["LINE_TOO_LONG"]  # 17 > 16
    zh_fast = _doc([{"id": "c1", "start": 0, "end": 1, "text": "这是十个字的中文句子"}], "zh-TW")
    assert [i.code for i in validate_document(zh_fast)] == ["READING_SPEED_TOO_HIGH"]  # 10 > 9 cps
    ko = _doc([{"id": "c1", "start": 0, "end": 1, "text": "안녕하세요 반갑습니다 여러분"}], "ko")
    # 13 hangul + 2 spaces * 0.5 = 14 units: fits 16, but 14 > 12 cps
    assert [i.code for i in validate_document(ko)] == ["READING_SPEED_TOO_HIGH"]


def test_ko_kinsoku_and_word_breaking():
    ko = PROFILES["ko"]
    assert break_lines("오늘은 날씨가 정말 좋네요 산책하러 갈까요?", ko, 16) == ["오늘은 날씨가 정말 좋네요", "산책하러 갈까요?"]
    issues = validate_document(_doc([{"id": "c1", "start": 0, "end": 5, "text": "정말요\n?"}], "ko"))
    assert [i.code for i in issues] == ["KINSOKU_LINE_START"]


def test_zh_kinsoku():
    issues = validate_document(_doc([{"id": "c1", "start": 0, "end": 5, "text": "他说《\n红楼梦》很好看"}], "zh"))
    assert [i.code for i in issues] == ["KINSOKU_LINE_END"]
    assert issues[0].suggested_text == "他说《红楼梦》很好看"


# ---- languages without a profile are unchanged ------------------------------


def test_languages_without_profile_keep_generic_counting():
    # 30 Japanese characters in an "en" document: len() counting, generic 42 / 20 defaults, no kinsoku
    text = "今日はとても良い天気ですね\n。散歩に行きましょう。"
    issues = validate_document(_doc([{"id": "c1", "start": 0, "end": 5, "text": text}], "en"))
    assert issues == []
    long_line = _doc([{"id": "c1", "start": 0, "end": 10, "text": "x" * (DEFAULT_MAX_CHARS_PER_LINE + 1)}], "en")
    [issue] = validate_document(long_line)
    assert issue.message == f"line 0 has {DEFAULT_MAX_CHARS_PER_LINE + 1} chars, exceeds max_chars_per_line=42"
    assert issue.to_dict() == {"severity": "warning", "code": "LINE_TOO_LONG", "message": issue.message, "cue_id": "c1"}
    fast = _doc([{"id": "c1", "start": 0, "end": 1, "text": "y" * 21}], "en")
    [issue] = validate_document(fast)
    assert issue.message == f"21 chars in 1.000s exceeds reading_speed_cps={DEFAULT_READING_SPEED_CPS}"


# ---- end to end through the request boundary --------------------------------


def test_generate_reports_locale_observations_without_changing_text(tmp_path):
    text = "今日はとても良い天気ですね\n。散歩に行きましょう。"
    resp = execute(
        {
            "operation": "generate",
            "format": "srt",
            "workspace": str(tmp_path),
            "output_path": "out.srt",
            "subtitle": {"id": "d", "language": "ja", "cues": [{"id": "c1", "start": 0, "end": 7, "text": text}]},
        }
    )
    assert resp["status"] == "ok"
    assert resp["observation"] == [
        {
            "severity": "warning",
            "code": "KINSOKU_LINE_START",
            "cue_id": "c1",
            "message": "line 1 starts with '。' (U+3002), which may not begin a line (locale profile 'ja')",
            "suggested_text": "今日はとても良い天気です\nね。散歩に行きましょう。",
        }
    ]
    assert text in (tmp_path / "out.srt").read_text(encoding="utf-8")  # reported, never auto-corrected
