"""Studio generate-wizard contract: init must not block on inherited stdin.

NitroStudio step 4 of 5 used to hang because `nitrostack-py init --skip-install`
still prompted Description/Author via readline() on an inherited stdin that
never wrote. --skip-install only skips dependency install.

These tests spawn the real CLI with stdin left open (PIPE, never written, never
closed) so they fail if prompts return.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _cli_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run_init_open_stdin(cwd: str, extra_args: list[str], timeout: float = 20.0):
    """Spawn init with stdin PIPE left open. Do not write or close it."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "nitrostack.cli.main", *extra_args],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
    )
    chunks_out: list[bytes] = []
    chunks_err: list[bytes] = []

    def _drain(stream, bucket):
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
                bucket.append(data)
        except Exception:
            pass

    threading.Thread(target=_drain, args=(proc.stdout, chunks_out), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, chunks_err), daemon=True).start()

    deadline = time.time() + timeout
    while proc.poll() is None:
        if time.time() > deadline:
            proc.kill()
            proc.wait(timeout=5)
            stdout = b"".join(chunks_out).decode("utf-8", "replace")
            stderr = b"".join(chunks_err).decode("utf-8", "replace")
            raise TimeoutError(
                f"CLI hung for {timeout}s on open unused stdin\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.05)

    stdout = b"".join(chunks_out).decode("utf-8", "replace")
    stderr = b"".join(chunks_err).decode("utf-8", "replace")
    return proc.returncode, stdout, stderr


def test_init_skip_install_does_not_hang_on_open_unused_stdin():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-studio-")
    try:
        code, stdout, stderr = _run_init_open_stdin(
            tmp,
            ["init", "studio_demo", "--template", "python-starter", "--skip-install"],
        )
        assert code == 0, f"exit {code}\n{stdout}\n{stderr}"
        project = Path(tmp) / "studio_demo"
        assert (project / "main.py").is_file()
        assert (project / "app_module.py").is_file()
        req = (project / "requirements.txt").read_text(encoding="utf-8")
        assert "nitrostack" in req.lower()
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def test_init_existing_dir_exits_without_overwrite_prompt():
    tmp = tempfile.mkdtemp(prefix="nitro-cli-exists-")
    try:
        os.mkdir(os.path.join(tmp, "already"))
        code, stdout, stderr = _run_init_open_stdin(
            tmp,
            ["init", "already", "--template", "python-starter", "--skip-install"],
            timeout=15.0,
        )
        assert code != 0
        combined = (stdout + stderr).lower()
        assert "already exists" in combined
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
