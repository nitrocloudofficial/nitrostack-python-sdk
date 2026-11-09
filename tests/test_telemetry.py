"""PostHog CLI telemetry: exact payloads, opt-out, and stub ingest."""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from nitrostack.cli import telemetry as telemetry_mod
from nitrostack.cli.telemetry import (
    identify_from_cloud_login,
    maybe_print_first_run_notice,
    resolved_posthog_api_key,
    shutdown_telemetry,
    track_command_invoked,
    track_deploy_completed,
    track_deploy_failed,
    track_event,
    track_init_completed,
)


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    """Isolate module globals between tests."""
    telemetry_mod._client = None  # noqa: SLF001
    telemetry_mod._posthog_unavailable = False  # noqa: SLF001
    telemetry_mod._anonymous_distinct_id = None  # noqa: SLF001
    telemetry_mod._distinct_id_override = None  # noqa: SLF001
    telemetry_mod._shutdown_done = False  # noqa: SLF001
    yield
    telemetry_mod._client = None  # noqa: SLF001
    telemetry_mod._posthog_unavailable = False  # noqa: SLF001
    telemetry_mod._anonymous_distinct_id = None  # noqa: SLF001
    telemetry_mod._distinct_id_override = None  # noqa: SLF001
    telemetry_mod._shutdown_done = False  # noqa: SLF001


@pytest.fixture
def telemetry_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NITROSTACK_POSTHOG_KEY", "phc_test_stub_key")
    monkeypatch.setenv("NITROSTACK_POSTHOG_HOST", "https://stub.example")
    monkeypatch.setenv("NITROSTACK_ENVIRONMENT", "development")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    cfg = tmp_path / ".config" / "nitrostack"
    cfg.mkdir(parents=True)
    flag = cfg / ".telemetry_first_run_notice_shown"
    flag.write_text("1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(telemetry_mod)
    yield
    importlib.reload(telemetry_mod)


def _track_with_mock_client(telemetry_env, monkeypatch):
    """Return a MagicMock wired as the telemetry PostHog client."""
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "get_distinct_id", lambda: "test-distinct-id")
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    return mock


def test_opt_out_do_not_track(telemetry_env, monkeypatch):
    mock = _track_with_mock_client(telemetry_env, monkeypatch)
    track_event("cli_command_invoked", {"command": "baseline"})
    assert mock.capture.call_count == 1

    monkeypatch.setenv("DO_NOT_TRACK", "1")
    importlib.reload(telemetry_mod)
    mock.reset_mock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_event("cli_command_invoked", {"command": "x"})
    mock.capture.assert_not_called()


def test_opt_out_telemetry_flag_sends_nothing(telemetry_env, monkeypatch):
    mock = _track_with_mock_client(telemetry_env, monkeypatch)
    track_event("cli_command_invoked", {"command": "baseline"})
    assert mock.capture.call_count == 1

    monkeypatch.setenv("NITROSTACK_TELEMETRY", "0")
    importlib.reload(telemetry_mod)
    mock.reset_mock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_event("cli_command_invoked", {"command": "x"})
    mock.capture.assert_not_called()


def test_ci_default_off_without_explicit_enable(telemetry_env, monkeypatch):
    mock = _track_with_mock_client(telemetry_env, monkeypatch)
    track_event("cli_command_invoked", {"command": "baseline"})
    assert mock.capture.call_count == 1

    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("NITROSTACK_TELEMETRY", raising=False)
    importlib.reload(telemetry_mod)
    mock.reset_mock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_event("cli_command_invoked", {"command": "x"})
    mock.capture.assert_not_called()


def test_posthog_missing_is_silent_noop(telemetry_env, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name == "posthog":
            raise ImportError("posthog not installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(telemetry_mod)
    track_event("cli_command_invoked", {"command": "x"})
    assert telemetry_mod._posthog_unavailable is True  # noqa: SLF001
    track_event("cli_command_invoked", {"command": "y"})


def test_builtin_posthog_key_used_when_env_unset(monkeypatch):
    """Built-in public project key when NITROSTACK_POSTHOG_KEY is unset (no network)."""
    monkeypatch.delenv("NITROSTACK_POSTHOG_KEY", raising=False)
    assert resolved_posthog_api_key() == telemetry_mod.DEFAULT_POSTHOG_PROJECT_KEY


def test_track_event_payload_exact(telemetry_env, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "get_distinct_id", lambda: "abc123deadbeef")
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_event("cli_command_invoked", {"command": "validate"})
    mock.capture.assert_called_once()
    call_kw = mock.capture.call_args.kwargs
    assert call_kw["distinct_id"] == "abc123deadbeef"
    assert call_kw["event"] == "cli_command_invoked"
    props = call_kw["properties"]
    assert props["product"] == "python_sdk"
    assert props["surface"] == "terminal"
    assert props["environment"] == "development"
    assert props["command"] == "validate"
    assert "app_version" in props
    assert "python_version" in props
    assert props["sdk_version"] == props["app_version"]
    assert "os_platform" in props
    assert "os_arch" in props


def test_cli_command_invoked_helper(telemetry_env, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_command_invoked("pack")
    mock.capture.assert_called_once()
    assert mock.capture.call_args.kwargs["event"] == "cli_command_invoked"
    assert mock.capture.call_args.kwargs["properties"]["command"] == "pack"


def test_cli_init_completed_properties(telemetry_env, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_init_completed(
        template="python-starter",
        skip_install=True,
        duration_ms=42,
        has_custom_description=False,
    )
    props = mock.capture.call_args.kwargs["properties"]
    assert mock.capture.call_args.kwargs["event"] == "cli_init_completed"
    assert props["template"] == "python-starter"
    assert props["skip_install"] is True
    assert props["duration_ms"] == 42
    assert props["has_custom_description"] is False


def test_cli_deploy_completed_and_failed(telemetry_env, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    track_deploy_completed()
    assert mock.capture.call_args_list[-1].kwargs["event"] == "cli_deploy_completed"
    track_deploy_failed(error_code="RuntimeError")
    assert mock.capture.call_args_list[-1].kwargs["properties"]["error_code"] == "RuntimeError"


def test_identify_login_emits_alias_identify_group_and_event(telemetry_env, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(telemetry_mod, "_get_client", lambda: mock)
    monkeypatch.setattr(telemetry_mod, "_anonymous_id_from_machine", lambda: "anonhex1234567890")
    identify_from_cloud_login(
        user_id="user-cloud-1",
        email="person@example.com",
        organization_id="org-42",
    )
    mock.alias.assert_called_once_with(previous_id="anonhex1234567890", distinct_id="user-cloud-1")
    mock.identify.assert_called_once_with(
        distinct_id="user-cloud-1",
        properties={"email": "person@example.com"},
    )
    mock.group_identify.assert_called_once()
    g_call = mock.group_identify.call_args
    assert g_call.args[0] == "organization"
    assert g_call.args[1] == "org-42"
    assert g_call.kwargs.get("distinct_id") == "user-cloud-1"
    login_capture = [
        c
        for c in mock.capture.call_args_list
        if c.kwargs.get("event") == "cli_login_completed"
    ]
    assert len(login_capture) == 1
    p = login_capture[0].kwargs["properties"]
    assert p["user_email"] == "person@example.com"
    assert p["cloud_user_id"] == "user-cloud-1"


def _decode_post_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if handler.headers.get("Content-Encoding") == "gzip":
        return zlib.decompress(raw, 16 + zlib.MAX_WBITS)
    return raw


def test_stub_batch_server_records_payload(telemetry_env, monkeypatch):
    """End-to-end: PostHog client to local HTTP stub (no real PostHog)."""
    bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path not in ("/batch", "/capture", "/e", "/i/v0/e"):
                self.send_error(404)
                return
            bodies.append(_decode_post_body(self))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("NITROSTACK_POSTHOG_HOST", host)

    importlib.reload(telemetry_mod)

    track_command_invoked("validate")
    shutdown_telemetry(timeout=5.0)
    server.shutdown()
    thread.join(timeout=2.0)

    assert len(bodies) >= 1
    outer = json.loads(bodies[0].decode("utf-8"))
    batch = outer.get("batch") if isinstance(outer, dict) else outer
    assert isinstance(batch, list)
    events = [x for x in batch if isinstance(x, dict) and x.get("event") == "cli_command_invoked"]
    assert len(events) == 1
    props = events[0]["properties"]
    assert props["command"] == "validate"
    assert props["product"] == "python_sdk"


def test_maybe_print_skips_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(telemetry_mod)
    buf = io.StringIO()
    with (
        patch.dict(os.environ, {"DO_NOT_TRACK": "1"}, clear=False),
        patch.object(sys, "stderr", buf),
    ):
        importlib.reload(telemetry_mod)
        maybe_print_first_run_notice()
    assert buf.getvalue() == ""
