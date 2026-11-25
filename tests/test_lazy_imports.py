"""Import-time regression: public API and CLI should stay light."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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
