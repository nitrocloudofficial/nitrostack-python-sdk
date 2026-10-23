"""`nitrostack-py upgrade` on projects shaped like `init` creates them.

`init` vendors the SDK into vendor/nitrostack and points uv at it with a
`[tool.uv.sources]` entry, so upgrade must replace that copy and must not
touch the source entry.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nitrostack.cli import vendor
from nitrostack.cli._shared import loads_toml
from nitrostack.cli.upgrade import (
    UpgradeError,
    fetch_nitrostack_wheel,
    replace_nitrostack_spec,
    upgrade_project,
)
from nitrostack.cli.vendor import (
    ensure_vendored_nitrostack,
    extract_nitrostack_wheel,
    vendored_version,
)

INIT_PYPROJECT = (
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.10"\n'
    "dependencies = [\n"
    '    "nitrostack",\n'
    "]\n\n"
    "[tool.uv.sources]\n"
    'nitrostack = { path = "vendor/nitrostack" }\n'
)


def _wheel(version: str, extra_files: dict | None = None) -> bytes:
    files = {
        "nitrostack/__init__.py": f"VERSION = {version!r}\n",
        "nitrostack/transports/assets/landing.html": "<html></html>",
        "nitrostack/templates/starter/main.py": "print('template')\n",
        f"nitrostack-{version}.dist-info/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: nitrostack\n"
            f"Version: {version}\n"
            "Requires-Dist: mcp<3,>=2\n"
            "Requires-Dist: pydantic>=2.0.0\n"
            'Requires-Dist: tomli>=2.0.0; python_version < "3.11"\n'
            'Requires-Dist: pytest>=7.4.0; extra == "dev"\n'
        ),
    }
    files.update(extra_files or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def no_skills():
    with patch(
        "nitrostack.cli.upgrade.upgrade_agent_skills", return_value={"skipped": True}
    ) as mock:
        yield mock


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(INIT_PYPROJECT, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("nitrostack\n", encoding="utf-8")
    assert ensure_vendored_nitrostack(str(tmp_path), quiet=True)
    return tmp_path


def test_spec_rewrite_skips_the_uv_source_entry(tmp_path: Path, no_skills):
    # Not vendored, but the pyproject still names nitrostack in [tool.uv.sources].
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(INIT_PYPROJECT, encoding="utf-8")
    with patch("nitrostack.cli.upgrade.verify_nitrostack_version"):
        upgrade_project(str(tmp_path), version="1.2.3", verify=False, allow_downgrade=True)
    parsed = loads_toml(pyproject.read_text(encoding="utf-8"))
    assert parsed["project"]["dependencies"] == ["nitrostack==1.2.3"]
    assert parsed["tool"]["uv"]["sources"]["nitrostack"] == {"path": "vendor/nitrostack"}


def test_spec_rewrite_skips_vendor_paths():
    text = "-e ./vendor/nitrostack\nnitrostack>=0.3.0\n"
    updated, count = replace_nitrostack_spec(text, "nitrostack>=9.9.9")
    assert count == 1
    assert updated == "-e ./vendor/nitrostack\nnitrostack>=9.9.9\n"


def test_upgrade_replaces_the_vendored_sdk_from_the_wheel(project: Path, no_skills):
    pyproject_before = (project / "pyproject.toml").read_text(encoding="utf-8")
    with (
        patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value="9.9.9"),
        patch(
            "nitrostack.cli.upgrade.fetch_nitrostack_wheel", return_value=_wheel("9.9.9")
        ) as fetch,
    ):
        result = upgrade_project(str(project))

    fetch.assert_called_once_with("9.9.9")
    assert result["written"] == ["vendor/nitrostack"]
    assert result["changes"][0]["to"] == "9.9.9"
    assert vendored_version(str(project)) == "9.9.9"
    sdk = project / "vendor" / "nitrostack"
    assert (sdk / "nitrostack" / "__init__.py").read_text(encoding="utf-8") == "VERSION = '9.9.9'\n"
    assert not (sdk / "nitrostack" / "templates").exists()
    deps = loads_toml((sdk / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    assert deps == ["mcp<3,>=2", "pydantic>=2.0.0", 'tomli>=2.0.0; python_version < "3.11"']
    # The project keeps the plain name; the vendored copy decides the version.
    assert (project / "pyproject.toml").read_text(encoding="utf-8") == pyproject_before
    assert (project / "requirements.txt").read_text(encoding="utf-8") == "nitrostack\n"
    assert not [p for p in (project / "vendor").iterdir() if p.name.startswith(".nitrostack-")]


def test_upgrade_to_the_running_version_copies_it(project: Path, no_skills):
    running = vendor._distribution_version()
    (project / "vendor" / "nitrostack" / "pyproject.toml").write_text(
        '[project]\nname = "nitrostack"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    with (
        patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value=running),
        patch("nitrostack.cli.upgrade.fetch_nitrostack_wheel") as fetch,
    ):
        upgrade_project(str(project))
    fetch.assert_not_called()
    assert vendored_version(str(project)) == running


def test_upgrade_vendored_dry_run_same_version_and_downgrade(project: Path, no_skills):
    current = vendored_version(str(project))
    snapshot = sorted(p.relative_to(project) for p in project.rglob("*"))

    with (
        patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value="9.9.9"),
        patch("nitrostack.cli.upgrade.fetch_nitrostack_wheel") as fetch,
    ):
        dry = upgrade_project(str(project), dry_run=True)
    fetch.assert_not_called()
    assert dry["changes"] == [{"file": "vendor/nitrostack", "from": current, "to": "9.9.9"}]
    assert dry["written"] == []

    with (
        patch("nitrostack.cli.upgrade.verify_nitrostack_version"),
        patch("nitrostack.cli.upgrade.fetch_nitrostack_wheel") as fetch,
    ):
        same = upgrade_project(str(project), version=current)
        with pytest.raises(UpgradeError, match="allow-downgrade"):
            upgrade_project(str(project), version="0.0.1")
    fetch.assert_not_called()
    assert same["changes"] == [] and same["written"] == []
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == snapshot


def test_failed_download_or_bad_wheel_keeps_the_current_sdk(project: Path, no_skills):
    current = vendored_version(str(project))
    with patch("nitrostack.cli.upgrade.fetch_latest_nitrostack_version", return_value="9.9.9"):
        with patch(
            "nitrostack.cli.upgrade.fetch_nitrostack_wheel", side_effect=UpgradeError("offline")
        ):
            with pytest.raises(UpgradeError, match="offline"):
                upgrade_project(str(project))
        with patch("nitrostack.cli.upgrade.fetch_nitrostack_wheel", return_value=b"not a zip"):
            with pytest.raises(zipfile.BadZipFile):
                upgrade_project(str(project))
    assert vendored_version(str(project)) == current
    assert (
        project / "vendor" / "nitrostack" / "nitrostack" / "transports" / "assets" / "landing.html"
    ).is_file()
    assert not [p for p in (project / "vendor").iterdir() if p.name.startswith(".nitrostack-")]


def test_extract_refuses_paths_outside_the_vendor_dir(tmp_path: Path):
    evil = _wheel("9.9.9", {"nitrostack/../../escaped.py": "boom"})
    with pytest.raises(RuntimeError, match="outside"):
        extract_nitrostack_wheel(evil, tmp_path / "sdk")
    assert not (tmp_path / "escaped.py").exists()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _pypi(wheel: bytes, digest: str):
    payload = {
        "urls": [
            {
                "packagetype": "sdist",
                "filename": "nitrostack-9.9.9.tar.gz",
                "url": "https://x/sdist",
                "digests": {},
            },
            {
                "packagetype": "bdist_wheel",
                "filename": "nitrostack-9.9.9-py3-none-any.whl",
                "url": "https://x/wheel",
                "digests": {"sha256": digest},
            },
        ]
    }

    def urlopen(request, timeout=None):
        url = request.full_url
        return _Response(json.dumps(payload).encode() if url.endswith("/json") else wheel)

    return urlopen


def test_fetch_wheel_checks_sha256():
    wheel = _wheel("9.9.9")
    good = hashlib.sha256(wheel).hexdigest()
    with patch("urllib.request.urlopen", side_effect=_pypi(wheel, good)):
        assert fetch_nitrostack_wheel("9.9.9") == wheel
    with patch("urllib.request.urlopen", side_effect=_pypi(wheel, "0" * 64)):
        with pytest.raises(UpgradeError, match="sha256"):
            fetch_nitrostack_wheel("9.9.9")
