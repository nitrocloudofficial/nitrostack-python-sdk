"""eMudhra/emSign remediation: secure pip bootstrap and uv version gate."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.cli.install import (  # noqa: E402
    SECURE_PIP_SPEC,
    _check_uv_version,
    _ensure_secure_pip,
    _get_pip_version,
    _parse_version,
    _run_pip,
    _run_uv,
)

MOD = "nitrostack.cli.install"


def _ok(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail() -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr="")


# ---- version parsing ------------------------------------------------------

def test_parse_pip_two_and_three_part_versions():
    assert _parse_version("pip 24.0 from /x/pip (python 3.12)", "pip") == (24, 0, 0)
    assert _parse_version("pip 26.2.2 from /x/pip (python 3.12)", "pip") == (26, 2, 2)


def test_parse_uv_version_with_build_suffix():
    assert _parse_version("uv 0.13.4 (abc123 2026-10-20)", "uv") == (0, 13, 4)


def test_parse_garbage_returns_none():
    assert _parse_version("command not found", "pip") is None
    assert _parse_version("", "uv") is None


def test_get_pip_version_none_when_command_fails():
    with patch(f"{MOD}.subprocess.run", return_value=_fail()):
        assert _get_pip_version("python") is None


def test_get_pip_version_none_when_python_missing():
    with patch(f"{MOD}.subprocess.run", side_effect=OSError("nope")):
        assert _get_pip_version("python") is None


# ---- pip bootstrap --------------------------------------------------------

@pytest.mark.parametrize("version", [(26, 2, 2), (26, 3, 0), (27, 0, 0)])
def test_pip_already_secure_skips_bootstrap(version, tmp_path):
    with patch(f"{MOD}._get_pip_version", return_value=version):
        with patch(f"{MOD}.subprocess.run") as run:
            _ensure_secure_pip("python", str(tmp_path))
    run.assert_not_called()


def test_outdated_pip_is_upgraded_with_pinned_wheel(tmp_path):
    with patch(f"{MOD}._get_pip_version", side_effect=[(24, 0, 0), (26, 2, 2)]):
        with patch(f"{MOD}.subprocess.run", return_value=_ok()) as run:
            _ensure_secure_pip("python", str(tmp_path))
    run.assert_called_once()
    assert run.call_args.args[0] == ["python", "-m", "pip", "install", SECURE_PIP_SPEC]
    assert "#sha256=" in SECURE_PIP_SPEC


def test_unknown_pip_version_triggers_upgrade(tmp_path):
    with patch(f"{MOD}._get_pip_version", side_effect=[None, (26, 2, 2)]):
        with patch(f"{MOD}.subprocess.run", return_value=_ok()) as run:
            _ensure_secure_pip("python", str(tmp_path))
    run.assert_called_once()


def test_pip_upgrade_failure_raises_actionable_error(tmp_path):
    with patch(f"{MOD}._get_pip_version", return_value=(24, 0, 0)):
        with patch(f"{MOD}.subprocess.run", return_value=_fail()):
            with pytest.raises(RuntimeError, match="Secure pip upgrade failed"):
                _ensure_secure_pip("python", str(tmp_path))


def test_pip_still_old_after_upgrade_raises(tmp_path):
    with patch(f"{MOD}._get_pip_version", side_effect=[(24, 0, 0), (24, 0, 0)]):
        with patch(f"{MOD}.subprocess.run", return_value=_ok()):
            with pytest.raises(RuntimeError, match="still below 26.2.2"):
                _ensure_secure_pip("python", str(tmp_path))


def test_pip_version_unverifiable_after_upgrade_raises(tmp_path):
    with patch(f"{MOD}._get_pip_version", side_effect=[(24, 0, 0), None]):
        with patch(f"{MOD}.subprocess.run", return_value=_ok()):
            with pytest.raises(RuntimeError, match="still below 26.2.2"):
                _ensure_secure_pip("python", str(tmp_path))


def test_run_pip_stops_before_installing_when_bootstrap_fails(tmp_path):
    with patch(f"{MOD}.ensure_project_venv", return_value="python"):
        with patch(f"{MOD}._ensure_secure_pip", side_effect=RuntimeError("boom")):
            with patch(f"{MOD}.subprocess.run") as run:
                with pytest.raises(RuntimeError, match="boom"):
                    _run_pip(["-e", "."], cwd=str(tmp_path))
    run.assert_not_called()


def test_run_pip_bootstraps_before_installing(tmp_path):
    order: list[str] = []
    with patch(f"{MOD}.ensure_project_venv", return_value="python"):
        with patch(f"{MOD}._ensure_secure_pip", side_effect=lambda *a, **k: order.append("bootstrap")):
            with patch(
                f"{MOD}.subprocess.run",
                side_effect=lambda *a, **k: order.append("install") or _ok(),
            ):
                _run_pip(["-e", "."], cwd=str(tmp_path))
    assert order == ["bootstrap", "install"]


# ---- uv gate --------------------------------------------------------------

@pytest.mark.parametrize("version", [(0, 13, 4), (0, 14, 0), (1, 0, 0)])
def test_uv_meeting_minimum_is_allowed(version):
    with patch(f"{MOD}._get_uv_version", return_value=version):
        _check_uv_version("/usr/bin/uv")


@pytest.mark.parametrize("version", [(0, 13, 3), (0, 9, 0), None])
def test_old_or_unknown_uv_is_blocked(version):
    with patch(f"{MOD}._get_uv_version", return_value=version):
        with pytest.raises(RuntimeError, match="0.13.4"):
            _check_uv_version("/usr/bin/uv")


def test_run_uv_blocked_before_any_download(tmp_path):
    with patch(f"{MOD}._uv_bin", return_value="/usr/bin/uv"):
        with patch(f"{MOD}._get_uv_version", return_value=(0, 13, 3)):
            with patch(f"{MOD}.subprocess.run") as run:
                with pytest.raises(RuntimeError, match="uv 0.13.4"):
                    _run_uv(["sync"], cwd=str(tmp_path))
    run.assert_not_called()


def test_run_uv_runs_when_version_ok(tmp_path):
    with patch(f"{MOD}._uv_bin", return_value="/usr/bin/uv"):
        with patch(f"{MOD}._get_uv_version", return_value=(0, 13, 4)):
            with patch(f"{MOD}.subprocess.run", return_value=_ok()) as run:
                _run_uv(["sync"], cwd=str(tmp_path))
    run.assert_called_once()
    assert run.call_args.args[0] == ["/usr/bin/uv", "sync"]