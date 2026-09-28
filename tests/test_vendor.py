from __future__ import annotations

import os
from pathlib import Path

import pytest

from nitrostack.cli.vendor import (
    copy_running_nitrostack,
    ensure_vendored_nitrostack,
    pin_project_to_vendor,
    running_nitrostack_root,
)


def test_running_nitrostack_root_is_the_sdk_checkout():
    root = running_nitrostack_root()
    assert (root / "nitrostack" / "transports" / "assets" / "landing.html").is_file()


def test_copy_running_nitrostack_includes_landing_page(tmp_path: Path):
    dest = tmp_path / "vendor" / "nitrostack"
    copy_running_nitrostack(dest)
    assert (dest / "nitrostack" / "transports" / "assets" / "landing.html").is_file()
    assert (dest / "nitrostack" / "transports" / "assets" / "logo.png").is_file()
    assert not (dest / "nitrostack" / "templates").exists()


def test_ensure_vendored_pins_pyproject_and_requirements(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "y-server"\ndependencies = ["nitrostack"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("nitrostack>=0.3.2\n", encoding="utf-8")
    dest = ensure_vendored_nitrostack(str(tmp_path), quiet=True)
    assert dest and os.path.isdir(dest)
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dependencies = ["nitrostack"]' in pyproject
    assert "file:./vendor" not in pyproject
    assert 'nitrostack = { path = "vendor/nitrostack" }' in pyproject
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8").strip() == "nitrostack"
    pin_project_to_vendor(tmp_path)
    again = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert again.count('nitrostack = { path = "vendor/nitrostack" }') == 1
    assert "file:./vendor" not in again


def test_pin_strips_file_url_that_breaks_cloud_uv_wheels(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "my-app"\ndependencies = ["nitrostack @ file:./vendor/nitrostack"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("nitrostack @ file:./vendor/nitrostack\n", encoding="utf-8")
    (tmp_path / "vendor" / "nitrostack").mkdir(parents=True)
    pin_project_to_vendor(tmp_path)
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dependencies = ["nitrostack"]' in text
    assert "file:./vendor" not in text
    assert 'nitrostack = { path = "vendor/nitrostack" }' in text
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8").strip() == "nitrostack"


def test_minimal_pyproject_uses_distribution_version(tmp_path: Path, monkeypatch):
    from nitrostack.cli import vendor

    monkeypatch.setattr(vendor, "_distribution_version", lambda: "9.9.9")
    dest = tmp_path / "sdk"
    vendor._write_minimal_pyproject(dest)
    text = (dest / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "9.9.9"' in text
    assert "0.3.2" not in text


def test_distribution_version_has_no_hardcoded_fallback(monkeypatch):
    import nitrostack
    from nitrostack.cli.vendor import _distribution_version

    monkeypatch.delattr(nitrostack, "__version__", raising=False)

    def _missing(_name: str) -> str:
        raise Exception("missing")

    monkeypatch.setattr("importlib.metadata.version", _missing)
    with pytest.raises(RuntimeError, match="package version"):
        _distribution_version()


def test_ensure_vendored_skips_the_sdk_checkout():
    root = running_nitrostack_root()
    assert ensure_vendored_nitrostack(str(root), quiet=True) is None
