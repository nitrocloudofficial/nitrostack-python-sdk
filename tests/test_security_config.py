"""eMudhra/emSign remediation: lockfile, dependency floor and CI workflow guards."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.cli.install import SECURE_PIP_SPEC  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_CERTIFI = Version("2026.10.19")


def test_lock_resolves_fixed_certifi():
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [Version(p["version"]) for p in data["package"] if p["name"] == "certifi"]
    assert versions, "certifi missing from uv.lock"
    assert all(v >= MIN_CERTIFI for v in versions), versions


def test_pyproject_declares_certifi_floor():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "certifi>=2026.10.19" in data["project"]["dependencies"]


def test_requirements_declares_certifi_floor():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "certifi>=2026.10.19" in [line.strip() for line in lines]


@pytest.mark.parametrize(
    "name, later_command",
    [
        ("test.yml", 'pip install -e ".[dev]"'),
    ],
)

def test_workflow_bootstraps_secure_pip_before_other_installs(name, later_command):
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    assert SECURE_PIP_SPEC in text, "workflow pip spec must match install.SECURE_PIP_SPEC"
    assert 'python -m pip install --upgrade "certifi>=2026.10.19"' in text
    assert "pip install --upgrade pip" not in text
    assert text.index(SECURE_PIP_SPEC) < text.index(later_command)
