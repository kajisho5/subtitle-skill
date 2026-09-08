"""Render/burn-in tests against ffmpeg-skill's real `caption`/`probe` contract.

These run the actual, vendored ffmpeg-skill scripts (see
tests/fixtures/ffmpeg_skill_vendor/README.md for provenance) rather than a
hand-rolled stub: `subtitle_skill.engine` was written and verified against
ffmpeg-skill's real CLI contract (docs/contract.md, scripts/_contract.py,
scripts/caption.py -- confirmed by running them, not by reading docs alone),
and these tests exercise that same code, unmodified, end to end.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

VENDOR_ROOT = Path(__file__).parent / "fixtures" / "ffmpeg_skill_vendor"


@pytest.fixture()
def ffmpeg_skill_install(tmp_path, monkeypatch):
    """Copy the vendored ffmpeg-skill scripts into a fresh 'install' dir and
    point subtitle_skill.engine at it -- exactly how a real ~/.claude/skills/
    ffmpeg-skill install looks to subtitle-skill.
    """
    install_dir = tmp_path / "ffmpeg-skill-install"
    shutil.copytree(VENDOR_ROOT / "scripts", install_dir / "scripts")
    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(install_dir))
    return install_dir


def _make_tiny_video(path: Path, duration: float = 1.0) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("real ffmpeg is required for render tests (ffmpeg-skill/caption always needs it)")
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _make_video_with_audio_streams(path: Path, n: int, duration: float = 1.0) -> None:
    """A video with `n` distinct-frequency audio tracks, so --audio-stream N
    selecting a real, distinguishable track can be verified end to end."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("real ffmpeg is required for render tests (ffmpeg-skill/caption always needs it)")
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration}"]
    for i in range(n):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 * (i + 1)}:duration={duration}"]
    cmd += ["-map", "0:v"]
    for i in range(n):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def _ffprobe_streams(path: Path) -> list:
    ffprobe = shutil.which("ffprobe")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)["streams"]


def _base_request(tmp_path, output_path="out.mp4"):
    return {
        "operation": "render",
        "workspace": str(tmp_path),
        "format": "srt",
        "output_path": output_path,
        "video_input": "in.mp4",
        "subtitle": {
            "id": "d",
            "language": "en",
            "cues": [{"id": "c1", "start": 0, "end": 1, "text": "hello world"}],
        },
    }


def test_ffmpeg_skill_not_found_is_dependency_error(tmp_path, monkeypatch):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(tmp_path / "nowhere"))
    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    with pytest.raises(SubtitleSkillError) as exc:
        execute(_base_request(tmp_path))
    assert exc.value.code == "DEPENDENCY_ERROR"


def test_render_only_accepts_srt(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)
    request = _base_request(tmp_path)
    request["format"] = "vtt"

    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_render_delegates_to_real_ffmpeg_skill_caption(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute
    from subtitle_skill.provenance import sha256_file

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    response = execute(_base_request(tmp_path))
    assert response["status"] == "ok"
    assert response["engine"] == "ffmpeg-skill"
    # engine_version must be a non-empty string even though this vendored
    # fixture has no package.json -- a real downstream consumer
    # (video-production-agent's SubtitleAdapter, verified against its
    # actual source) treats a falsy engine_version on a render response
    # as an invalid result, not a render failure.
    assert isinstance(response["engine_version"], str) and response["engine_version"]

    out = tmp_path / "out.mp4"
    assert out.exists() and out.stat().st_size > 0
    assert response["sha256"] == sha256_file(out)

    # not just exit 0: the source video is actually gone through ffmpeg-skill's
    # real `caption` tool, which reports its own ffprobe of the output.
    ffprobe = shutil.which("ffprobe")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    duration = float(json.loads(proc.stdout)["format"]["duration"])
    assert duration == pytest.approx(1.0, abs=0.25)


def test_render_command_actually_used_the_subtitles_filter(tmp_path, ffmpeg_skill_install, monkeypatch):
    """Confirm the real transformation happened, not merely that the process exited 0.

    subtitle-skill cannot verify caption *content* pixel-by-pixel (that is
    ffmpeg-skill/look plus human or agent judgement -- explicitly out of
    subtitle-skill's deterministic scope), but it can and does capture
    ffmpeg-skill's own reported command line, which must reference the
    subtitles filter and our generated SRT file.
    """
    import subtitle_skill.engine as engine_module

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)
    output_path = tmp_path / "out.mp4"
    srt_path = tmp_path / "cues.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello world\n\n", encoding="utf-8")

    response = engine_module.burn_in(
        video_path=video_path,
        subtitle_path=srt_path,
        subtitle_format="srt",
        output_path=output_path,
    )
    commands = response.get("commands", [])
    assert commands, "ffmpeg-skill/caption reported no ffmpeg command line"
    assert any("subtitles=" in c and str(srt_path) in c for c in commands)


def test_render_reuse(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = _base_request(tmp_path)
    first = execute(request)
    second = execute(request)
    assert first["reused"] is False
    assert second["reused"] is True
    assert first["sha256"] == second["sha256"]


def test_render_rejects_video_with_no_video_stream(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("real ffmpeg is required")
    audio_path = tmp_path / "in.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-vn", str(audio_path)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(SubtitleSkillError) as exc:
        execute(_base_request(tmp_path))
    assert exc.value.code == "INVALID_INPUT"


def test_render_missing_video_input_file(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    with pytest.raises(SubtitleSkillError) as exc:
        execute(_base_request(tmp_path))
    assert exc.value.code == "MISSING_INPUT"


def test_render_identity_survives_a_version_bump_with_unchanged_script(tmp_path, ffmpeg_skill_install):
    """A bare package.json version bump, with byte-identical caption.py/
    _common.py, must NOT invalidate the cache: nothing that actually runs
    changed. The identity anchor is the executed scripts' content hash,
    not the self-reported version string (see engine.ffmpeg_skill_script_hash).
    """
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)
    (ffmpeg_skill_install / "package.json").write_text(json.dumps({"version": "0.9.1"}), encoding="utf-8")

    first = execute(_base_request(tmp_path))
    assert first["reused"] is False
    assert first["engine_version"] == "0.9.1"

    (ffmpeg_skill_install / "package.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
    second = execute(_base_request(tmp_path))
    assert second["reused"] is True
    # a cache hit skips ffmpeg-skill entirely, so the display field is
    # whatever was recorded at render time, not the current package.json
    assert second["engine_version"] == "0.9.1"


def test_render_identity_changes_when_script_content_changes(tmp_path, ffmpeg_skill_install):
    """The real bug this guards against: a hand-patched caption.py (or
    _common.py) with an untouched package.json must still invalidate the
    cache, because what actually executes is different -- provenance must
    not be fooled by an unchanged version string.
    """
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    first = execute(_base_request(tmp_path))
    assert first["reused"] is False

    second = execute(_base_request(tmp_path))
    assert second["reused"] is True

    # patch _common.py's behavior without touching package.json (there is
    # none in this fixture) or caption.py itself
    common_path = ffmpeg_skill_install / "scripts" / "_common.py"
    common_path.write_text(common_path.read_text(encoding="utf-8") + "\n# patched\n", encoding="utf-8")

    third = execute(_base_request(tmp_path))
    assert third["reused"] is False


@pytest.mark.skipif(os.name == "nt", reason="fake ffmpeg shell script is not directly runnable on Windows")
def test_ffmpeg_failure_maps_to_tool_error(tmp_path, ffmpeg_skill_install, monkeypatch):
    """ffmpeg-skill's error.kind == "ffmpeg" (the ffmpeg process itself failed,
    as opposed to a bad input or a missing tool) must map to TOOL_ERROR, not a
    blanket DEPENDENCY_ERROR -- confirmed by making the real ffmpeg binary
    fail, not by asserting on engine.py's internals.
    """
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    real_ffprobe = shutil.which("ffprobe")
    (fake_bin / "ffmpeg").write_text("#!/bin/sh\necho 'fake ffmpeg failure' >&2\nexit 1\n")
    (fake_bin / "ffmpeg").chmod(0o755)
    shutil.copy(real_ffprobe, fake_bin / "ffprobe")
    (fake_bin / "ffprobe").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(SubtitleSkillError) as exc:
        execute(_base_request(tmp_path))
    assert exc.value.code == "TOOL_ERROR"


def test_render_rejects_cue_past_real_video_duration_even_without_hint(tmp_path, ffmpeg_skill_install):
    """The real bug this guards against: request.video_duration is an
    optional caller hint. If the caller omits it (or gets it wrong), a cue
    past the actual end of the video must still be caught -- otherwise it
    renders "successfully" with the caption silently never shown (libass
    just drops cues past the video's end) and no observation at all.
    """
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path, duration=1.0)  # real duration: 1.0s

    request = _base_request(tmp_path)
    request["subtitle"]["cues"] = [{"id": "c1", "start": 0.0, "end": 5.0, "text": "past the end of the video"}]
    # deliberately no request["video_duration"] at all

    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "VALIDATION_ERROR"


def test_render_accepts_cue_within_real_video_duration_without_hint(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path, duration=2.0)

    request = _base_request(tmp_path)
    request["subtitle"]["cues"] = [{"id": "c1", "start": 0.0, "end": 1.5, "text": "within bounds"}]

    response = execute(request)
    assert response["status"] == "ok"


# --- mode="mux", audio_stream, and document.language forwarding (subtitle-skill#3 fix B) ---
# Exercised against the re-vendored real caption.py (0.12.2, subtitle-skill#3 fix A), which is
# where --mode mux / --audio-stream / mux's --language tagging actually live.


def test_render_mux_mode_adds_a_soft_toggleable_subtitle_stream(tmp_path, ffmpeg_skill_install):
    """mode="mux" must not burn pixels: it must add a separate, player-toggleable
    subtitle stream and leave the video/audio streams themselves untouched."""
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path, duration=1.0)

    request = _base_request(tmp_path)
    request["mode"] = "mux"

    response = execute(request)
    assert response["status"] == "ok"
    assert response["mode"] == "mux"

    out = tmp_path / "out.mp4"
    streams = _ffprobe_streams(out)
    subtitle_streams = [s for s in streams if s["codec_type"] == "subtitle"]
    assert len(subtitle_streams) == 1
    # .mp4 output -> mux_subtitle_codec() picks mov_text (see caption.py)
    assert subtitle_streams[0]["codec_name"] == "mov_text"
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    assert len(video_streams) == 1


def test_render_mux_mode_forwards_document_language_as_the_subtitle_tag(tmp_path, ffmpeg_skill_install):
    """SubtitleDocument.language is required and BCP47-validated (models.py) but was
    previously never read downstream (subtitle-skill#3). render with mode="mux" must
    now forward it as the newly added subtitle stream's language metadata tag.

    Output container is .mkv, not .mp4, here: confirmed directly against the real
    ffmpeg binary in this environment that the MOV/MP4 muxer (mov_text, caption.py's
    codec choice for .mp4/.m4v/.mov) silently drops a `-metadata:s:s:N language=...`
    value that isn't a 3-letter ISO 639-2 code -- a plain 2-letter BCP47 tag like
    "ja"/"en" (exactly what subtitle-skill's own `_is_bcp47_ish` accepts and what
    real callers will send) is accepted by caption.py and by ffmpeg (exit 0, no
    warning) but never actually lands in the .mp4's stream tags. Matroska's muxer
    writes whatever string it's given verbatim, so it isolates subtitle-skill's own
    forwarding logic (what this test is actually about) from that unrelated MOV/MP4
    muxer quirk. subtitle-skill does not normalize/reject the language value -- it
    forwards exactly what the document says, matching its "no editorial decisions"
    mandate -- so a caller who needs the tag to actually persist in an .mp4/.mov
    output should pass a 3-letter code, or use .mkv/.webm.
    """
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path, duration=1.0)

    request = _base_request(tmp_path, output_path="out.mkv")
    request["mode"] = "mux"
    request["subtitle"]["language"] = "ja"

    response = execute(request)
    assert response["status"] == "ok"

    out = tmp_path / "out.mkv"
    subtitle_streams = [s for s in _ffprobe_streams(out) if s["codec_type"] == "subtitle"]
    assert len(subtitle_streams) == 1
    assert subtitle_streams[0].get("tags", {}).get("language") == "ja"


def test_render_burn_mode_does_not_tag_a_language(tmp_path, ffmpeg_skill_install):
    """--language only means something for --mode mux (or --transcribe, which
    subtitle-skill never uses); a plain burn-mode render must not send it, and the
    output (all pixels, no subtitle stream at all) has nowhere to tag it anyway."""
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path, duration=1.0)

    request = _base_request(tmp_path)
    request["subtitle"]["language"] = "ja"  # default mode is "burn"

    response = execute(request)
    assert response["status"] == "ok"
    assert response["mode"] == "burn"

    out = tmp_path / "out.mp4"
    subtitle_streams = [s for s in _ffprobe_streams(out) if s["codec_type"] == "subtitle"]
    assert subtitle_streams == []


def test_engine_burn_in_mode_mux_uses_the_mode_flag_not_the_subtitles_filter(tmp_path, ffmpeg_skill_install):
    """Confirm the real caption.py command line, not just the resulting file:
    --mode mux must appear, and the burn-mode `subtitles=` video filter must not."""
    import subtitle_skill.engine as engine_module

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)
    output_path = tmp_path / "out.mp4"
    srt_path = tmp_path / "cues.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello world\n\n", encoding="utf-8")

    response = engine_module.burn_in(
        video_path=video_path,
        subtitle_path=srt_path,
        subtitle_format="srt",
        output_path=output_path,
        mode="mux",
        language="en",
    )
    commands = response.get("commands", [])
    assert commands, "ffmpeg-skill/caption reported no ffmpeg command line"
    assert any("mov_text" in c for c in commands)
    assert not any("subtitles=" in c for c in commands)
    assert any("language=en" in c for c in commands)


def test_engine_burn_in_audio_stream_is_forwarded_to_the_real_command_line(tmp_path, ffmpeg_skill_install):
    """--audio-stream N must select the requested 0-based track, verified against
    ffmpeg-skill's own reported command line on a real multi-audio-track input."""
    import subtitle_skill.engine as engine_module

    video_path = tmp_path / "in.mp4"
    _make_video_with_audio_streams(video_path, n=2)
    output_path = tmp_path / "out.mp4"
    srt_path = tmp_path / "cues.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello world\n\n", encoding="utf-8")

    response = engine_module.burn_in(
        video_path=video_path,
        subtitle_path=srt_path,
        subtitle_format="srt",
        output_path=output_path,
        audio_stream=1,
    )
    commands = response.get("commands", [])
    assert commands
    assert any("0:a:1" in c for c in commands)
    assert not any("0:a:0" in c for c in commands)


def test_render_audio_stream_selects_the_requested_track_end_to_end(tmp_path, ffmpeg_skill_install):
    """Same as above, but through the full request/response path (subtitle_skill.operations.execute)
    rather than calling engine.burn_in directly."""
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_video_with_audio_streams(video_path, n=2)

    request = _base_request(tmp_path)
    request["audio_stream"] = 1

    response = execute(request)
    assert response["status"] == "ok"

    out = tmp_path / "out.mp4"
    audio_streams = [s for s in _ffprobe_streams(out) if s["codec_type"] == "audio"]
    assert len(audio_streams) == 1  # only the requested track is kept


def test_render_audio_stream_out_of_range_is_invalid_input(tmp_path, ffmpeg_skill_install):
    """caption.py itself probes the input and rejects an out-of-range --audio-stream
    as kind="input"; that must map to subtitle-skill's INVALID_INPUT, not a generic
    DEPENDENCY_ERROR or TOOL_ERROR."""
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_video_with_audio_streams(video_path, n=1)

    request = _base_request(tmp_path)
    request["audio_stream"] = 5

    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "INVALID_INPUT"


def test_render_invalid_mode_rejected_before_touching_ffmpeg_skill(tmp_path, ffmpeg_skill_install):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = _base_request(tmp_path)
    request["mode"] = "reencode-everything"  # not a real caption.py --mode choice

    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "INVALID_INPUT"
    assert not (tmp_path / "out.mp4").exists()


@pytest.mark.parametrize("bad_audio_stream", [-1, "0", 1.5, True])
def test_render_invalid_audio_stream_type_rejected(tmp_path, ffmpeg_skill_install, bad_audio_stream):
    from subtitle_skill.operations import execute
    from subtitle_skill.errors import SubtitleSkillError

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    request = _base_request(tmp_path)
    request["audio_stream"] = bad_audio_stream

    with pytest.raises(SubtitleSkillError) as exc:
        execute(request)
    assert exc.value.code == "INVALID_INPUT"


def test_render_identity_differs_between_burn_and_mux_mode(tmp_path, ffmpeg_skill_install):
    """The same document/video rendered with mode="burn" then mode="mux" must NOT be
    treated as a cache hit -- they are different outputs (pixels vs. a soft track)."""
    from subtitle_skill.operations import execute

    video_path = tmp_path / "in.mp4"
    _make_tiny_video(video_path)

    burn_request = _base_request(tmp_path)
    first = execute(burn_request)
    assert first["reused"] is False
    assert first["mode"] == "burn"

    mux_request = _base_request(tmp_path)
    mux_request["mode"] = "mux"
    second = execute(mux_request)
    assert second["reused"] is False
    assert second["mode"] == "mux"
    assert first["sha256"] != second["sha256"]
