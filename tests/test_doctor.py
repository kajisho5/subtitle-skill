"""doctor --json: render availability must reflect ffmpeg-skill's real, dynamically
detected capabilities (per its own `caption` tool requirements), not just whether
some ffmpeg-skill install directory happens to exist.
"""
import shutil
from pathlib import Path

import pytest

VENDOR_ROOT = Path(__file__).parent / "fixtures" / "ffmpeg_skill_vendor"


@pytest.fixture()
def ffmpeg_skill_install(tmp_path, monkeypatch):
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts")
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def test_doctor_reports_render_unavailable_when_ffmpeg_skill_not_found(tmp_path, monkeypatch):
    from subtitle_skill.doctor import build_doctor_report

    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(tmp_path / "nowhere"))
    report = build_doctor_report()
    assert "render" not in report["supported_operations"]
    assert "generate" in report["supported_operations"]
    assert report["dependencies"]["ffmpeg-skill"]["available"] is False
    assert not report["healthy"]
    assert any(p["code"] == "DEPENDENCY_ERROR" for p in report["problems"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires real ffmpeg for capability detection")
def test_doctor_reports_render_available_when_ffmpeg_skill_and_ffmpeg_present(ffmpeg_skill_install):
    from subtitle_skill.doctor import build_doctor_report

    report = build_doctor_report()
    assert "render" in report["supported_operations"]
    assert report["dependencies"]["ffmpeg-skill"]["available"] is True
    caps = report["dependencies"]["ffmpeg-skill"]["capabilities"]
    assert caps["checked"] is True
    assert report["render_supported_formats"] == ["srt"]


def test_doctor_render_formats_never_include_vtt(tmp_path, monkeypatch):
    from subtitle_skill.doctor import build_doctor_report

    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(tmp_path / "nowhere"))
    report = build_doctor_report()
    assert "vtt" not in report["render_supported_formats"]
    assert "srt" in report["supported_formats"]
    assert "vtt" in report["supported_formats"]
