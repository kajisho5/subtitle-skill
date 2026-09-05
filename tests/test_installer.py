"""Agent Skill installer: places SKILL.md for Claude Code / Cursor / Codex
discovery, mirroring ffmpeg-skill's bin/install.js flag convention.
"""
from pathlib import Path

from subtitle_skill.installer import install

REPO_ROOT_SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def test_packaged_skill_md_matches_repo_root_exactly():
    """The packaged copy (installed into site-packages, read by `install`)
    must never silently drift from the repo-root SKILL.md that GitHub,
    humans, and README links point to.
    """
    import subtitle_skill.installer as installer_module

    packaged = installer_module._skill_md_bytes()
    assert packaged == REPO_ROOT_SKILL_MD.read_bytes()


def test_default_target_is_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = install()
    assert len(results) == 1
    assert results[0].label == "Claude Code"
    installed = tmp_path / ".claude" / "skills" / "subtitle-skill" / "SKILL.md"
    assert installed.exists()
    assert installed.read_bytes() == REPO_ROOT_SKILL_MD.read_bytes()


def test_all_installs_to_three_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = install(all_=True)
    assert {r.label for r in results} == {"Claude Code", "Cursor", "Codex"}
    for agent_dir in ("claude", "cursor", "codex"):
        assert (tmp_path / f".{agent_dir}" / "skills" / "subtitle-skill" / "SKILL.md").exists()


def test_custom_dir(tmp_path):
    results = install(custom_dir=str(tmp_path / "my-skills"))
    assert results[0].directory == str(tmp_path / "my-skills" / "subtitle-skill")
    assert (tmp_path / "my-skills" / "subtitle-skill" / "SKILL.md").exists()


def test_project_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = install(project=True)
    assert (tmp_path / ".claude" / "skills" / "subtitle-skill" / "SKILL.md").exists()
    assert results[0].label.startswith("project")


def test_uninstall_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    install(claude=True)
    target = tmp_path / ".claude" / "skills" / "subtitle-skill"
    assert target.exists()

    results = install(claude=True, uninstall=True)
    assert not target.exists()
    assert results[0].action == "removed"


def test_uninstall_of_nonexistent_target_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    results = install(claude=True, uninstall=True)
    assert results[0].action == "removed"
