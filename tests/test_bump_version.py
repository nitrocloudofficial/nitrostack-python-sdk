from __future__ import annotations

import importlib.util
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "bump_version.py")


def _load():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = _load()


def test_bump_x_y_z():
    assert bump.bump_x_y_z("0.3.5", "patch") == "0.3.6"
    assert bump.bump_x_y_z("0.3.5", "minor") == "0.4.0"
    assert bump.bump_x_y_z("0.3.5", "major") == "1.0.0"


def test_plan_bump_skips_unpublished_git_version():
    assert bump.plan_bump("0.3.6", "0.3.5", {"0.3.5"}, "patch") is None


def test_plan_bump_from_pypi_when_git_is_stale():
    released = {"0.3.2", "0.3.4", "0.3.5"}
    assert bump.plan_bump("0.3.2", "0.3.5", released, "patch") == "0.3.6"


def test_plan_bump_patch_when_git_matches_pypi():
    assert bump.plan_bump("0.3.5", "0.3.5", {"0.3.5"}, "patch") == "0.3.6"


def test_should_publish():
    assert bump.should_publish("0.3.6", {"0.3.5"}) is True
    assert bump.should_publish("0.3.5", {"0.3.5"}) is False


def test_apply_version_syncs_pyproject_and_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "nitrostack"\nversion = "0.3.2"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "annotated-types"\nversion = "0.8.0"\n\n'
        '[[package]]\nname = "nitrostack"\nversion = "0.3.2"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    bump.apply_version(str(tmp_path), "0.3.6")
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'version = "0.3.6"' in pyproject
    assert 'name = "nitrostack"\nversion = "0.3.6"\nsource = { editable = "." }' in lock
    assert 'name = "annotated-types"\nversion = "0.8.0"' in lock


def test_set_project_version_only_first_field():
    text = '[project]\nversion = "0.3.2"\n\n[tool.ruff]\ntarget-version = "py310"\n'
    assert bump.read_project_version(bump.set_project_version(text, "0.3.6")) == "0.3.6"
