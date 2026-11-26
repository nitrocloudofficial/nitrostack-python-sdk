"""Integration test for nitrostack-py dev hot reload."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _list_calculate_description(port: int) -> str:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {"name": "pytest-dev-reload", "version": "1"},
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/list",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode()
    if raw.startswith("{"):
        body = json.loads(raw)
    else:
        body = json.loads(next(line[5:].strip() for line in raw.splitlines() if line.startswith("data:")))
    return next(tool["description"] for tool in body["result"]["tools"] if tool["name"] == "calculate")


def _wait_for_health(port: int, timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp/health", timeout=2) as resp:
                if json.loads(resp.read().decode()).get("status") == "ok":
                    return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.2)
    raise AssertionError(f"MCP server did not become healthy on port {port}")


@pytest.mark.timeout(30)
def test_dev_hot_reload_updates_tool_description(tmp_path: Path) -> None:
    port = 47640
    marker = "PYTEST_DEV_RELOAD"
    env = {**os.environ, "DO_NOT_TRACK": "1"}
    init = subprocess.run(
        [sys.executable, "-m", "nitrostack.cli.main", "init", "app", "--template", "python-starter", "--skip-install"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr or init.stdout
    project = tmp_path / "app"
    tool_file = project / "modules" / "calculator" / "calculator_tools.py"
    original = tool_file.read_text(encoding="utf-8")
    (project / ".env").write_text("MCP_TRANSPORT_TYPE=http\nDO_NOT_TRACK=1\n", encoding="utf-8")
    needle = "Perform basic arithmetic calculations"

    dev = subprocess.Popen(
        [sys.executable, "-m", "nitrostack.cli.main", "dev", "--port", str(port)],
        cwd=project,
        env={**env, "MCP_TRANSPORT_TYPE": "http"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(port)
        assert marker not in _list_calculate_description(port)
        tool_file.write_text(original.replace(needle, f"{needle} {marker}", 1), encoding="utf-8")

        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if marker in _list_calculate_description(port):
                    break
            except urllib.error.URLError:
                pass
            time.sleep(0.4)
        else:
            pytest.fail("calculate tool description did not reload within 10s")

        dev.send_signal(signal.SIGINT)
        dev.wait(timeout=8)
        assert dev.returncode is not None
        if sys.platform != "win32":
            # Parent must not leave the HTTP child listening after shutdown.
            time.sleep(0.5)
            with pytest.raises((urllib.error.URLError, ConnectionError)):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp/health", timeout=1):
                    pass
    finally:
        tool_file.write_text(original, encoding="utf-8")
        if dev.poll() is None:
            dev.kill()
            dev.wait(timeout=3)

def test_stop_child_never_signals_a_group_it_did_not_create(monkeypatch):
    """A fake or non-leader process must not reach os.killpg (pid 1 is init's group)."""
    from unittest.mock import MagicMock

    from nitrostack.cli import _servers

    sent = []
    monkeypatch.setattr(_servers.os, "killpg", lambda *a: sent.append(a))
    fake = MagicMock()
    fake.poll.return_value = None
    for pid in (MagicMock(), 0, 1, os.getpid()):
        fake.pid = pid
        _servers._stop_child(fake)
    assert sent == []
    assert fake.terminate.called
