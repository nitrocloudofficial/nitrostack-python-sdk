from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from nitrostack.cli.skills import (
    AGENT_SKILL_DIRS,
    SkillsCloneError,
    clone_skills_repo as _real_clone_skills_repo,
    discover_skills,
    install_skills,
    read_local_skills_version,
    run_skills_flow,
    upgrade_agent_skills,
    write_local_skills_version,
)
from nitrostack.cli.upgrade import upgrade_project


def _fake_clone(tmp_path: Path, version: str = "1.0.0", names: tuple[str, ...] = ("mcp-app-architecture",)) -> Path:
    clone = tmp_path / "clone"
    (clone / "skills").mkdir(parents=True)
    (clone / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    for name in names:
        skill = clone / "skills" / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(f"# {name}\nfrom nitrostack import module\n", encoding="utf-8")
    return clone


@pytest.fixture
def disable_live_skills_clone():
    """Override autouse block so this module can install from a fake clone."""
    yield


def test_discover_skills_lists_skill_dirs_only(tmp_path: Path):
    clone = _fake_clone(tmp_path, names=("auth-security", "mcp-app-architecture"))
    (clone / "skills" / ".hidden").mkdir()
    (clone / "skills" / "README.md").write_text("nope", encoding="utf-8")
    found = discover_skills(str(clone))
    assert [name for name, _ in found] == ["auth-security", "mcp-app-architecture"]


def test_install_skills_fans_out_and_skips_without_force(tmp_path: Path):
    clone = _fake_clone(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    skills = discover_skills(str(clone))
    install_skills(str(project), skills, force=False)
    for rel in AGENT_SKILL_DIRS:
        dest = project / rel / "mcp-app-architecture" / "SKILL.md"
        assert dest.is_file()
    marker = project / ".cursor" / "skills" / "mcp-app-architecture" / "SKILL.md"
    marker.write_text("keep-me", encoding="utf-8")
    install_skills(str(project), skills, force=False)
    assert marker.read_text(encoding="utf-8") == "keep-me"
    install_skills(str(project), skills, force=True)
    assert "from nitrostack import module" in marker.read_text(encoding="utf-8")


def test_run_skills_flow_clone_error_does_not_raise(tmp_path: Path, capsys):
    project = tmp_path / "proj"
    project.mkdir()

    def boom():
        raise SkillsCloneError("no git")

    with patch("nitrostack.cli.skills.clone_skills_repo", side_effect=boom):
        run_skills_flow(str(project), force=False)
    out = capsys.readouterr().out
    assert "Skipped agent skills" in out
    assert not (project / ".nitrostack.json").exists()


def test_run_skills_flow_writes_stamp(tmp_path: Path):
    clone = _fake_clone(tmp_path, version="1.2.3")
    project = tmp_path / "proj"
    project.mkdir()
    with patch("nitrostack.cli.skills.clone_skills_repo", return_value=str(clone)):
        run_skills_flow(str(project), force=False)
    assert read_local_skills_version(str(project)) == "1.2.3"
    assert (project / ".claude" / "skills" / "mcp-app-architecture" / "SKILL.md").is_file()


def test_init_mocked_clone_writes_nitrostack_json(tmp_path: Path, monkeypatch):
    from nitrostack.cli.main import init_project

    clone = _fake_clone(tmp_path / "remote", version="1.0.0")
    monkeypatch.setattr("nitrostack.cli.skills.clone_skills_repo", lambda: str(clone))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("desc\nauthor\n"))
    init_project("skills-demo", template="python-starter", skip_install=True)
    project = tmp_path / "skills-demo"
    stamp = json.loads((project / ".nitrostack.json").read_text(encoding="utf-8"))
    assert stamp["skillsVersion"] == "1.0.0"
    assert (project / ".cursor" / "skills" / "mcp-app-architecture" / "SKILL.md").is_file()


def test_upgrade_reinstalls_when_remote_newer(tmp_path: Path):
    clone = _fake_clone(tmp_path / "v2", version="2.0.0", names=("mcp-app-architecture", "ui-widgets"))
    project = tmp_path / "proj"
    project.mkdir()
    write_local_skills_version(str(project), "1.0.0")
    (project / ".cursor" / "skills" / "mcp-app-architecture").mkdir(parents=True)
    (project / ".cursor" / "skills" / "mcp-app-architecture" / "SKILL.md").write_text("old", encoding="utf-8")
    with patch("nitrostack.cli.skills.clone_skills_repo", return_value=str(clone)):
        result = upgrade_agent_skills(str(project), dry_run=False)
    assert result["upgraded"] is True
    assert read_local_skills_version(str(project)) == "2.0.0"
    body = (project / ".cursor" / "skills" / "mcp-app-architecture" / "SKILL.md").read_text(encoding="utf-8")
    assert body != "old"
    assert (project / ".agents" / "skills" / "ui-widgets" / "SKILL.md").is_file()


def test_upgrade_dry_run_does_not_install(tmp_path: Path):
    clone = _fake_clone(tmp_path / "v2", version="2.0.0")
    project = tmp_path / "proj"
    project.mkdir()
    write_local_skills_version(str(project), "1.0.0")
    with patch("nitrostack.cli.skills.clone_skills_repo", return_value=str(clone)):
        result = upgrade_agent_skills(str(project), dry_run=True)
    assert result["dry_run"] is True
    assert result["upgraded"] is False
    assert not (project / ".cursor" / "skills").exists()


def test_upgrade_project_refreshes_skills(tmp_path: Path):
    clone = _fake_clone(tmp_path / "v2", version="1.1.0")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        '    "nitrostack>=0.1.0",\n'
        "]\n",
        encoding="utf-8",
    )
    write_local_skills_version(str(tmp_path), "1.0.0")
    with patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value="9.9.9"):
        with patch("nitrostack.cli.skills.clone_skills_repo", return_value=str(clone)):
            result = upgrade_project(str(tmp_path), dry_run=True, verify=False)
    assert result["skills"]["remote"] == "1.1.0"
    assert result["skills"]["dry_run"] is True


def test_clone_skills_repo_wraps_git_errors():
    with patch("nitrostack.cli.skills.subprocess.run", side_effect=FileNotFoundError("git")):
        with pytest.raises(SkillsCloneError, match="Git is not installed"):
            _real_clone_skills_repo()
