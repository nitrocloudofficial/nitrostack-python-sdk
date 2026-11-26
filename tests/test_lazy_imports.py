"""Import-time regression: public API and CLI should stay light."""

import json
import pkgutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
NITROSTACK_ROOT = ROOT / "nitrostack"


def _legacy_subpackage_names() -> list[str]:
    """Subpackages reachable as ``nitrostack.<name>`` after eager import at 1790572."""
    names = {m.name for m in pkgutil.iter_modules([str(NITROSTACK_ROOT)]) if not m.name.startswith("_")}
    for path in NITROSTACK_ROOT.iterdir():
        if path.is_dir() and not path.name.startswith("_") and path.name != "__pycache__":
            names.add(path.name)
    return sorted(names)


def test_import_nitrostack_avoids_heavy_optional_modules():
    script = """
import sys
import nitrostack
assert nitrostack.__version__
blocked = ("uvicorn", "cryptography", "quickjs", "posthog")
loaded = [name for name in blocked if name in sys.modules]
assert not loaded, f"eager imports: {loaded}"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT), "DO_NOT_TRACK": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_nitrostack_py_help_starts_quickly():
    cli = ROOT / ".venv" / "bin" / "nitrostack-py"
    if not cli.is_file():
        pytest.skip("venv CLI missing")
    env = {"DO_NOT_TRACK": "1", **dict(__import__("os").environ)}
    for _ in range(2):
        subprocess.run(
            [str(cli), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    started = time.perf_counter()
    result = subprocess.run(
        [str(cli), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result.returncode == 0, result.stderr
    assert elapsed_ms < 150, f"--help took {elapsed_ms:.0f} ms"


def test_subpackage_attributes_reachable_in_fresh_subprocess():
    subs = _legacy_subpackage_names()
    script = f"""
import nitrostack
subs = {json.dumps(subs)}
for name in subs:
    mod = getattr(nitrostack, name)
    assert mod.__name__ == f"nitrostack.{{name}}", (name, mod)
print("ok")
"""
    env = {**dict(__import__("os").environ), "PYTHONPATH": str(ROOT), "DO_NOT_TRACK": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lazy_export_patch_roundtrip():
    import nitrostack

    original = nitrostack.tool
    with patch("nitrostack.tool", autospec=True) as mocked:
        assert nitrostack.tool is mocked
    assert nitrostack.tool is original


def test_cli_module_entry_help_matches_main():
    env = {**dict(__import__("os").environ), "PYTHONPATH": str(ROOT), "DO_NOT_TRACK": "1"}
    cmd_main = [sys.executable, "-m", "nitrostack.cli.main"]
    cli = ROOT / ".venv" / "bin" / "nitrostack-py"
    if not cli.is_file():
        pytest.skip("venv CLI missing")
    for argv_suffix, expect_code in (
        (["--help"], 0),
        ([], 1),
        (["not-a-real-command"], 2),
    ):
        main_r = subprocess.run(
            [*cmd_main, *argv_suffix],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        cli_r = subprocess.run(
            [str(cli), *argv_suffix],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert main_r.returncode == cli_r.returncode == expect_code, (
            argv_suffix,
            main_r.stderr,
            cli_r.stderr,
        )
        if expect_code == 0:
            assert main_r.stdout == cli_r.stdout
