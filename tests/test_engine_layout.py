"""ffmpeg-skill moved `_common.py` into a `_common/` package in 2.0 and began
refusing to replace an existing output without `--overwrite` in 1.10; both
layouts must be found, and the flag passed only where it exists."""
from pathlib import Path


def _install(root: Path, *, package: bool, overwrite: bool) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "caption.py").write_text("# caption\n", encoding="utf-8")
    flag = 'g.add_argument("--overwrite")\n' if overwrite else "\n"
    if package:
        (scripts / "_common").mkdir()
        (scripts / "_common" / "__init__.py").write_text("\n", encoding="utf-8")
        (scripts / "_common" / "runner.py").write_text(flag, encoding="utf-8")
    else:
        (scripts / "_common.py").write_text(flag, encoding="utf-8")
    return root


def test_a_common_package_is_an_ffmpeg_skill_install(tmp_path, monkeypatch):
    from subtitle_skill import engine

    root = _install(tmp_path / "fs", package=True, overwrite=True)
    monkeypatch.setenv(engine.FFMPEG_SKILL_DIR_ENV, str(root))
    assert engine.resolve_ffmpeg_skill_root() == root
    assert engine._caption_takes_overwrite(root)


def test_a_single_common_module_still_resolves_without_overwrite(tmp_path, monkeypatch):
    from subtitle_skill import engine

    root = _install(tmp_path / "fs", package=False, overwrite=False)
    monkeypatch.setenv(engine.FFMPEG_SKILL_DIR_ENV, str(root))
    assert engine.resolve_ffmpeg_skill_root() == root
    assert not engine._caption_takes_overwrite(root)


def test_the_script_hash_follows_every_file_in_a_common_package(tmp_path):
    from subtitle_skill import engine

    root = _install(tmp_path / "fs", package=True, overwrite=True)
    before = engine.ffmpeg_skill_script_hash(root)
    (root / "scripts" / "_common" / "runner.py").write_text("# changed\n", encoding="utf-8")
    assert engine.ffmpeg_skill_script_hash(root) != before
