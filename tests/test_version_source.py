"""The version lives in exactly one place: pyproject.toml
(docs/adr/ADR-003-single-source-version.md). Everything that reports a version must
say the same thing, the release workflow's bump script must be enough to move all of
it, and the contract's ffmpeg-skill dependency comes from the one constant the
delegating code was written against."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import subtitle_skill
from subtitle_skill import CONTRACT_VERSION, SKILL_VERSION, __version__
from subtitle_skill.contract import build_contract
from subtitle_skill.doctor import build_doctor_report
from subtitle_skill.engine import FFMPEG_SKILL_CONTRACT_VERSION, FFMPEG_SKILL_TOOLS

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version(root: Path = ROOT) -> str:
    found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert found, "pyproject.toml has no version line"
    return found.group(1)


def _cli(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "subtitle_skill.cli", *args], capture_output=True, text=True, env=env, cwd=str(cwd))


def test_version_is_the_pyproject_version():
    assert __version__ == SKILL_VERSION == _pyproject_version()
    assert __version__ != "0.0.0+unknown"


def test_contract_and_doctor_report_the_pyproject_version(tmp_path):
    assert build_contract()["version"] == _pyproject_version()
    assert build_doctor_report()["version"] == _pyproject_version()
    for command in ("contract", "doctor"):
        proc = _cli([command, "--json"], tmp_path)
        assert proc.returncode in (0, 1), proc.stderr  # doctor exits 1 when ffmpeg-skill is absent
        assert json.loads(proc.stdout)["version"] == _pyproject_version()


def test_contract_version_is_unchanged():
    """Single-sourcing the package version does not touch the contract's own version."""
    assert build_contract()["contract_version"] == CONTRACT_VERSION == "1.0.0"


def test_ffmpeg_skill_dependency_is_a_lower_bound_from_the_engine_constant():
    deps = build_contract()["dependencies"]
    assert deps == [{
        "skill_id": "ffmpeg-skill",
        "version_range": f">={FFMPEG_SKILL_CONTRACT_VERSION}",
        "range_of": "contract_version",
        "required": False,
        "for": ["subtitle/render", "subtitle/mux"],
        "tools": [f"ffmpeg-skill/{tool}" for tool in FFMPEG_SKILL_TOOLS],
    }]
    assert deps[0]["version_range"] == ">=1.0"
    assert "<" not in deps[0]["version_range"]  # no ceiling, and never an exact pin
    assert build_contract()["operations"]["render"]["delegates_to"] == {"skill_id": "ffmpeg-skill", "tool": "caption"}
    assert "ffmpeg-skill/caption" in deps[0]["tools"]


def _version_in(python_path: Path, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(python_path)
    code = ("import subtitle_skill; from subtitle_skill.contract import build_contract; c = build_contract(); "
            "assert c['version'] == subtitle_skill.__version__ == subtitle_skill.SKILL_VERSION; print(c['version'])")
    # -S: no site-packages, so an installed (e.g. editable) subtitle-skill cannot answer in place of `python_path`
    proc = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, env=env, cwd=str(cwd))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_release_bump_script_moves_every_reported_version(tmp_path):
    """Simulate release.yml's auto-bump on a copy of this checkout: scripts/bump_version.py rewrites only
    pyproject.toml, and __version__, the contract and doctor all follow (the script checks that itself too)."""
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["NEW_VERSION"] = "9.8.7"
    proc = subprocess.run([sys.executable, "-S", str(ROOT / "scripts" / "bump_version.py")], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert _pyproject_version(tmp_path) == "9.8.7"
    assert _version_in(tmp_path / "src", tmp_path) == "9.8.7"


def test_release_bump_script_refuses_a_non_semver_version(tmp_path):
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    env = dict(os.environ, NEW_VERSION="1.2")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bump_version.py")], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path))
    assert proc.returncode == 1
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_installed_copy_without_pyproject_reads_distribution_metadata(tmp_path):
    """An installed wheel has no pyproject.toml next to it: the version then comes from the
    distribution metadata (built from that same pyproject.toml); with none, 0.0.0+unknown."""
    site = tmp_path / "site"
    shutil.copytree(Path(subtitle_skill.__file__).parent, site / "subtitle_skill", ignore=shutil.ignore_patterns("__pycache__"))
    assert _version_in(site, tmp_path) == "0.0.0+unknown"
    dist_info = site / "subtitle_skill-4.5.6.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: subtitle-skill\nVersion: 4.5.6\n", encoding="utf-8")
    assert _version_in(site, tmp_path) == "4.5.6"
