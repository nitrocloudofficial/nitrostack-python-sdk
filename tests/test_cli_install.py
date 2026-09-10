"""Phase 5 gap — `nitrostack-py install` without invoking a real pip."""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.cli.install import (
    _optional_extra_names,
    _run_pip,
    ensure_project_venv,
    install_dependencies,
    pep503_name,
    requirements_uses_local_nitrostack,
    venv_python,
)


@pytest.fixture(autouse=True)
def _uv_absent(monkeypatch):
    """Keep the existing pip-path tests off a real uv binary on PATH."""
    monkeypatch.setattr("nitrostack.cli.install._uv_bin", lambda: None)


def test_optional_extra_names_parses_pyproject():
    text = """
[project]
name = "demo"

[project.optional-dependencies]
dev = ["pytest"]
test = ["httpx"]

[tool.ruff]
line-length = 100
"""
    assert _optional_extra_names(text) == ["dev", "test"]


def test_optional_extra_names_empty_when_section_missing():
    assert _optional_extra_names("[project]\nname = 'x'\n") == []


def test_install_requires_project_manifest(tmp_path):
    with pytest.raises(RuntimeError, match="pyproject.toml or requirements.txt"):
        install_dependencies(cwd=str(tmp_path))


def test_install_editable_with_extras(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n\n[project.optional-dependencies]\ndev = ['pytest']\n",
        encoding="utf-8",
    )
    with patch("nitrostack.cli.install._run_pip") as run_pip:
        install_dependencies(cwd=str(tmp_path), production=False)
    run_pip.assert_called_once_with(["-e", ".[dev]"], cwd=str(tmp_path))


def test_install_production_skips_extras_and_dev_files(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    with patch("nitrostack.cli.install._run_pip") as run_pip:
        install_dependencies(cwd=str(tmp_path), production=True)
    run_pip.assert_called_once_with(["-e", "."], cwd=str(tmp_path))


def test_install_requirements_txt_and_dev_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("starlette\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    with patch("nitrostack.cli.install._run_pip") as run_pip:
        install_dependencies(cwd=str(tmp_path), production=False)
    assert run_pip.call_args_list[0].args[0] == ["-r", str(tmp_path / "requirements.txt")]
    assert run_pip.call_args_list[1].args[0] == ["-r", str(tmp_path / "requirements-dev.txt")]


def test_run_pip_raises_on_nonzero_exit(tmp_path):
    with patch("nitrostack.cli.install.ensure_project_venv", return_value=sys.executable):
        with patch("nitrostack.cli.install.subprocess.run") as run:
            run.return_value.returncode = 3
            with pytest.raises(RuntimeError, match="exit code 3"):
                _run_pip(["-e", "."], cwd=str(tmp_path))


def test_ensure_project_venv_creates_and_reuses(tmp_path):
    python = venv_python(str(tmp_path))

    def fake_venv(cmd, **kwargs):
        os.makedirs(os.path.dirname(python), exist_ok=True)
        open(python, "w", encoding="utf-8").write("")
        return type("R", (), {"returncode": 0})()

    with patch("nitrostack.cli.install.subprocess.run", side_effect=fake_venv) as run:
        first = ensure_project_venv(str(tmp_path))
        second = ensure_project_venv(str(tmp_path))
    assert first == python == second
    run.assert_called_once()
    assert run.call_args.args[0][:3] == [sys.executable, "-m", "venv"]


def test_install_requirements_uses_venv_python(tmp_path):
    (tmp_path / "requirements.txt").write_text("nitrostack\n", encoding="utf-8")
    venv_py = venv_python(str(tmp_path))
    with patch("nitrostack.cli.install.ensure_project_venv", return_value=venv_py) as ensure:
        with patch("nitrostack.cli.install.subprocess.run") as run:
            run.return_value.returncode = 0
            install_dependencies(cwd=str(tmp_path), production=False)
    ensure.assert_called()
    cmd = run.call_args.args[0]
    assert cmd[0] == venv_py
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert cmd[4] == "-r"


def test_pep503_name_normalizes_folder():
    assert pep503_name("My_App") == "my-app"
    assert pep503_name("  Nitro Stack!  ") == "nitro-stack"
    assert pep503_name("") == "nitrostack-app"


def test_requirements_uses_local_nitrostack(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("nitrostack\n", encoding="utf-8")
    assert requirements_uses_local_nitrostack(str(req)) is False
    req.write_text("-e /path/to/nitrostack-python-sdk\n", encoding="utf-8")
    assert requirements_uses_local_nitrostack(str(req)) is True
    req.write_text("nitrostack @ file:///tmp/sdk\n", encoding="utf-8")
    assert requirements_uses_local_nitrostack(str(req)) is True
    req.write_text("./vendor/nitrostack\n", encoding="utf-8")
    assert requirements_uses_local_nitrostack(str(req)) is True
    assert requirements_uses_local_nitrostack(str(tmp_path / "missing.txt")) is False


def test_install_uses_uv_sync_when_uv_and_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\ndependencies = ['nitrostack']\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("nitrostack\n", encoding="utf-8")
    with patch("nitrostack.cli.install._uv_bin", return_value="/usr/bin/uv"):
        with patch("nitrostack.cli.install._run_uv") as run_uv:
            with patch("nitrostack.cli.install._run_pip") as run_pip:
                install_dependencies(cwd=str(tmp_path), production=False)
    run_uv.assert_called_once_with(["sync"], cwd=str(tmp_path))
    run_pip.assert_not_called()


def test_install_uv_production_passes_no_dev(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    with patch("nitrostack.cli.install._uv_bin", return_value="/usr/bin/uv"):
        with patch("nitrostack.cli.install._run_uv") as run_uv:
            install_dependencies(cwd=str(tmp_path), production=True)
    run_uv.assert_called_once_with(["sync", "--no-dev"], cwd=str(tmp_path))


def test_install_uses_pip_when_local_nitrostack_pin(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\ndependencies = ['nitrostack']\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "-e /path/to/nitrostack-python-sdk\n",
        encoding="utf-8",
    )
    with patch("nitrostack.cli.install._uv_bin", return_value="/usr/bin/uv"):
        with patch("nitrostack.cli.install._run_uv") as run_uv:
            with patch("nitrostack.cli.install._run_pip") as run_pip:
                install_dependencies(cwd=str(tmp_path), production=False)
    run_uv.assert_not_called()
    run_pip.assert_called_once_with(
        ["-r", str(tmp_path / "requirements.txt")],
        cwd=str(tmp_path),
    )
