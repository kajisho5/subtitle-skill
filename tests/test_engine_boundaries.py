"""Unit tests for engine.burn_in's own verification boundaries (duration
tolerance, missing video stream, zero-byte output) using a minimal FAKE
caption/probe pair that returns exactly the JSON we want to test against.

This is deliberately NOT testing ffmpeg-skill's real caption.py (that is
tests/test_engine_render.py, against the vendored/real scripts) -- it is
testing subtitle-skill's own arithmetic (the 0.25s tolerance boundary,
what happens when the reported probe has no video, etc.) precisely and
without depending on encoder-timing variance in a real ffmpeg run.
"""
import json
import textwrap
from pathlib import Path

import pytest


def _write_fake_scripts(root: Path, *, input_duration: float, output_duration, output_has_video: bool, write_output: bool = True):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "_common.py").write_text("# fake\n", encoding="utf-8")

    probe_src = textwrap.dedent(f"""
        import json, sys
        def main():
            path = sys.argv[1]
            if "in.mp4" in path:
                doc = {{"file": path, "duration": {input_duration!r}, "video": {{"codec": "h264"}}, "audio": None}}
            else:
                doc = {{"file": path, "duration": {output_duration!r}, "video": ({{"codec": "h264"}} if {output_has_video!r} else None), "audio": None}}
            print(json.dumps(doc))
        if __name__ == "__main__":
            main()
    """)
    (root / "scripts" / "probe.py").write_text(probe_src, encoding="utf-8")

    caption_src = textwrap.dedent(f"""
        import json, sys
        def main():
            args = sys.argv[1:]
            out_index = args.index("-o") + 1
            output_path = args[out_index]
            write_output = {write_output!r}
            if write_output:
                with open(output_path, "wb") as f:
                    f.write(b"fake mp4 bytes")
            probe = {{"file": output_path, "duration": {output_duration!r}, "video": ({{"codec": "h264"}} if {output_has_video!r} else None), "audio": None}}
            print(json.dumps({{"status": "completed", "output": output_path, "dry_run": False, "commands": ["fake ffmpeg cmd"], "probe": probe}}))
        if __name__ == "__main__":
            main()
    """)
    (root / "scripts" / "caption.py").write_text(caption_src, encoding="utf-8")


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    def _make(*, input_duration=10.0, output_duration=10.0, output_has_video=True, write_output=True):
        root = tmp_path / f"ffskill_{output_duration}_{output_has_video}_{write_output}".replace(".", "_").replace("-", "_")
        _write_fake_scripts(
            root,
            input_duration=input_duration,
            output_duration=output_duration,
            output_has_video=output_has_video,
            write_output=write_output,
        )
        monkeypatch.setenv("SUBTITLE_SKILL_FFMPEG_SKILL_DIR", str(root))
        return root

    return _make


def _burn_in(tmp_path):
    import subtitle_skill.engine as engine

    video_path = tmp_path / "in.mp4"
    video_path.write_bytes(b"fake input video")
    srt_path = tmp_path / "cues.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n", encoding="utf-8")
    output_path = tmp_path / "out.mp4"
    return engine.burn_in(video_path=video_path, subtitle_path=srt_path, subtitle_format="srt", output_path=output_path)


@pytest.mark.parametrize("output_duration", [10.0, 9.76, 10.24])
def test_duration_within_tolerance_accepted(tmp_path, fake_engine, output_duration):
    fake_engine(input_duration=10.0, output_duration=output_duration)
    response = _burn_in(tmp_path)
    assert response["probe"]["duration"] == output_duration


@pytest.mark.parametrize("output_duration", [9.7, 10.3, 8.0])
def test_duration_outside_tolerance_rejected(tmp_path, fake_engine, output_duration):
    from subtitle_skill.errors import SubtitleSkillError

    fake_engine(input_duration=10.0, output_duration=output_duration)
    with pytest.raises(SubtitleSkillError) as exc:
        _burn_in(tmp_path)
    assert exc.value.code == "OUTPUT_ERROR"


def test_output_with_no_video_stream_rejected(tmp_path, fake_engine):
    from subtitle_skill.errors import SubtitleSkillError

    fake_engine(input_duration=10.0, output_duration=10.0, output_has_video=False)
    with pytest.raises(SubtitleSkillError) as exc:
        _burn_in(tmp_path)
    assert exc.value.code == "OUTPUT_ERROR"


def test_reported_completed_but_no_file_written_rejected(tmp_path, fake_engine):
    from subtitle_skill.errors import SubtitleSkillError

    fake_engine(input_duration=10.0, output_duration=10.0, write_output=False)
    with pytest.raises(SubtitleSkillError) as exc:
        _burn_in(tmp_path)
    assert exc.value.code == "OUTPUT_ERROR"


def test_engine_version_is_never_null_without_package_json(tmp_path, fake_engine):
    """A known downstream consumer (video-production-agent's SubtitleAdapter)
    requires a render response's engine_version to be a non-empty string,
    treating a JSON null as an invalid result rather than a retryable
    failure. ffmpeg-skill installs without a package.json (a hand-built
    scripts/ directory, or this repo's own vendored test fixture) must
    therefore still produce a truthful, non-empty placeholder.
    """
    import subtitle_skill.engine as engine

    root = fake_engine(input_duration=10.0, output_duration=10.0)
    response = _burn_in(tmp_path)
    assert response["engine_skill_version"] == engine.UNKNOWN_ENGINE_VERSION
    assert isinstance(response["engine_skill_version"], str) and response["engine_skill_version"]
