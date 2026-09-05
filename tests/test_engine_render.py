"""Render/burn-in tests using a stub 'ffmpeg-skill' executable.

subtitle-skill must never build FFmpeg commands itself -- it only invokes
whatever execution skill is configured on PATH via that skill's own
`run - --json` contract. This stub plays the role of ffmpeg-skill so we
can test the delegation code path deterministically without depending on
ffmpeg-skill's real (out-of-scope) source. If a real `ffmpeg` binary is
present, the stub also performs a genuine burn-in so the test is a true
media E2E; otherwise it fabricates a minimal but valid output file so the
delegation contract itself is still exercised.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap

import pytest

STUB_SOURCE = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, shutil, subprocess, sys, os

    def main():
        assert sys.argv[1:3] == ["run", "-"]
        request = json.loads(sys.stdin.read())
        assert request["operation"] == "burn_in_subtitles"
        video = request["input_video"]
        subs = request["subtitle_file"]
        out = request["output_video"]

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            cmd = [ffmpeg, "-y", "-i", video, "-vf", f"subtitles={subs}", out]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                print(json.dumps({"status": "error", "message": proc.stderr[-2000:]}))
                return 1
        else:
            shutil.copyfile(video, out)

        print(json.dumps({"status": "ok", "output": out, "engine_version": "stub-1.0"}))
        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


@pytest.fixture()
def stub_ffmpeg_skill(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_path = bin_dir / "ffmpeg-skill"
    stub_path.write_text(STUB_SOURCE)
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IEXEC)

    wrapper = bin_dir / "ffmpeg-skill-wrapper"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {stub_path} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_BIN", str(wrapper))
    return wrapper


def _make_tiny_video(path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
            ],
            check=True,
            capture_output=True,
        )
    else:
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 256)  # not a real mp4, delegation-only test


def test_render_delegates_to_execution_skill(tmp_path, stub_ffmpeg_skill):
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello world"}],
        },
    }
    response = execute(request)
    assert response["status"] == "ok"
    assert response["engine"] == "ffmpeg-skill"
    out = tmp_path / "out.mp4"
    assert out.exists() and out.stat().st_size > 0
    assert response["sha256"] == __import__("subtitle_skill.provenance", fromlist=["sha256_file"]).sha256_file(out)


def test_render_reuse(tmp_path, stub_ffmpeg_skill):
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello world"}],
        },
    }
    first = execute(request)
    second = execute(request)
    assert first["reused"] is False
    assert second["reused"] is True
    assert first["sha256"] == second["sha256"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires real ffmpeg for a genuine media E2E")
def test_render_real_media_produces_playable_output(tmp_path, stub_ffmpeg_skill):
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": "out.mp4",
        "video_input": "in.mp4",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello world"}],
        },
    }
    response = execute(request)
    assert response["status"] == "ok"

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(tmp_path / "out.mp4")],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        duration = json.loads(proc.stdout)["format"]["duration"]
        assert float(duration) > 0
