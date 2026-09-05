import json
import subprocess
import sys

import pytest


def run_cli(args, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "subtitle_skill.cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=None,
        env=None,
    )


def test_contract_json_shape():
    proc = run_cli(["contract", "--json"])
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    for key in (
        "skill_id",
        "version",
        "contract_version",
        "operations",
        "capabilities",
        "parameters",
        "errors",
        "deterministic",
    ):
        assert key in data
    assert data["skill_id"] == "subtitle-skill"


def test_doctor_json_shape():
    proc = run_cli(["doctor", "--json"])
    data = json.loads(proc.stdout)
    for key in ("skill", "version", "supported_operations", "supported_formats", "dependencies", "problems"):
        assert key in data
    assert "render" not in data["supported_operations"] or data["dependencies"]["ffmpeg-skill"]["available"]


def test_run_generate_srt(tmp_path):
    request = {
        "operation": "generate",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.srt",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello"}],
        },
    }
    proc = run_cli(["run", "-", "--json"], stdin=json.dumps(request))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "ok"
    assert data["sha256"]
    assert (tmp_path / "out.srt").exists()


def test_run_generate_deterministic_and_reused(tmp_path):
    request = {
        "operation": "generate",
        "workspace": str(tmp_path),
        "format": "vtt",
        "output_path": "out.vtt",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello"}],
        },
    }
    raw = json.dumps(request)
    first = json.loads(run_cli(["run", "-", "--json"], stdin=raw).stdout)
    second = json.loads(run_cli(["run", "-", "--json"], stdin=raw).stdout)
    assert first["sha256"] == second["sha256"]
    assert first["reused"] is False
    assert second["reused"] is True


def test_run_malformed_json_is_typed_error():
    proc = run_cli(["run", "-", "--json"], stdin="{not json")
    data = json.loads(proc.stdout)
    assert data["status"] == "error"
    assert data["error"]["code"] == "INVALID_REQUEST"
    assert proc.returncode != 0


def test_run_missing_field_is_typed_error(tmp_path):
    request = {"operation": "generate", "workspace": str(tmp_path), "format": "srt"}
    proc = run_cli(["run", "-", "--json"], stdin=json.dumps(request))
    data = json.loads(proc.stdout)
    assert data["status"] == "error"
    assert data["error"]["code"] == "MISSING_INPUT"


def test_run_unsupported_operation(tmp_path):
    request = {
        "operation": "translate",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "o.srt",
        "subtitle": {"id": "d", "language": "en", "cues": [{"id": "c1", "start": 0, "end": 1, "text": "x"}]},
    }
    proc = run_cli(["run", "-", "--json"], stdin=json.dumps(request))
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_run_negative_start_rejected(tmp_path):
    request = {
        "operation": "generate",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "o.srt",
        "subtitle": {"id": "d", "language": "en", "cues": [{"id": "c1", "start": -1, "end": 1, "text": "x"}]},
    }
    proc = run_cli(["run", "-", "--json"], stdin=json.dumps(request))
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "INVALID_TIME_RANGE"


def test_run_render_without_engine_reports_dependency_error(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"not a real video, just bytes")
    request = {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": {"id": "d", "language": "en", "cues": [{"id": "c1", "start": 0, "end": 1, "text": "x"}]},
    }
    proc = run_cli(["run", "-", "--json"], stdin=json.dumps(request))
    data = json.loads(proc.stdout)
    if data["status"] == "error":
        assert data["error"]["code"] == "DEPENDENCY_ERROR"
