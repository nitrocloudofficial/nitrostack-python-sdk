"""Phase 2 — OAuth discovery metadata server (Python-only)."""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import DIContainer
from nitrostack.auth.oauth import OAuthModule, OAuthService, is_oauth_required, warn_if_oauth_fail_open


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()
    os.environ.pop("OAUTH_REQUIRED", None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_is_oauth_required_only_true_for_true_string(monkeypatch):
    monkeypatch.delenv("OAUTH_REQUIRED", raising=False)
    assert is_oauth_required() is False
    monkeypatch.setenv("OAUTH_REQUIRED", "false")
    assert is_oauth_required() is False
    monkeypatch.setenv("OAUTH_REQUIRED", "true")
    assert is_oauth_required() is True


def test_discovery_server_serves_protected_resource_metadata(monkeypatch):
    port = _free_port()
    monkeypatch.setenv("OAUTH_DISCOVERY_PORT", str(port))
    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    service.start_discovery_server()
    try:
        body = None
        for _ in range(20):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource",
                    timeout=0.5,
                ) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except OSError:
                time.sleep(0.05)
        assert body is not None
        assert body["resource"] == "http://localhost:3000/mcp"
        assert body["authorization_servers"] == ["http://auth.example/oauth"]
        assert body["scopes_supported"] == ["read"]

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/.well-known/oauth-authorization-server",
            timeout=0.5,
        ) as resp:
            as_meta = json.loads(resp.read().decode("utf-8"))
        assert as_meta["issuer"] == "http://auth.example/oauth"

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=0.5)
            raise AssertionError("unknown path should 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        service.stop_discovery_server()


def test_warn_if_oauth_fail_open_writes_once(monkeypatch, capsys):
    monkeypatch.delenv("OAUTH_REQUIRED", raising=False)
    import nitrostack.auth.oauth as oauth

    oauth._oauth_fail_open_warned = False
    warn_if_oauth_fail_open()
    warn_if_oauth_fail_open()
    err = capsys.readouterr().err
    assert "OAuth is configured" in err
    assert oauth._oauth_fail_open_warned is True
    oauth._oauth_fail_open_warned = False


def test_discovery_server_busy_port_with_retry(capsys):
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)

    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    try:
        service.start_discovery_server()
        assert service.bound_port is not None
        assert service.bound_port != port
        assert service.get_port() == service.bound_port
        assert service._server is not None

        err = capsys.readouterr().err
        assert f'"port": {service.bound_port}' in err

        with urllib.request.urlopen(
            f"http://127.0.0.1:{service.bound_port}/.well-known/oauth-protected-resource",
            timeout=1.0,
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["resource"] == "http://localhost:3000/mcp"
    finally:
        service.stop_discovery_server()
        blocker.close()


def test_discovery_server_retries_exhausted(capsys):
    import pytest

    port = _free_port()
    blockers = []
    for p in (port, port + 1):
        b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        b.bind(("127.0.0.1", p))
        b.listen(1)
        blockers.append(b)

    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
        discovery_max_retries=1,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    try:
        with pytest.raises(OSError):
            service.start_discovery_server()
        assert service._server is None
        assert service.bound_port is None
        assert service.get_port() is None
        err = capsys.readouterr().err
        assert f"Failed to start OAuth Discovery Server on port {port}" in err
    finally:
        service.stop_discovery_server()
        for b in blockers:
            b.close()


def test_discovery_server_auto_retry_disabled_surfaces_bind_failure(capsys, monkeypatch):
    import pytest

    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)

    monkeypatch.setenv("OAUTH_DISCOVERY_AUTO_RETRY", "false")
    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    try:
        with pytest.raises(OSError):
            service.start_discovery_server()
        assert service._server is None
        assert service.bound_port is None
        err = capsys.readouterr().err
        assert f"Failed to start OAuth Discovery Server on port {port}" in err
    finally:
        service.stop_discovery_server()
        blocker.close()


def test_discovery_server_reports_actual_bound_port(capsys):
    port = _free_port()
    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    try:
        service.start_discovery_server()
        assert service.bound_port == port
        assert service.get_port() == port
        err = capsys.readouterr().err
        assert f'"port": {port}' in err
    finally:
        service.stop_discovery_server()
        assert service.bound_port is None
        assert service.get_port() is None


def test_discovery_server_port_overflow_surfaces_oserror(capsys):
    import pytest

    port = 65535
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
    except OSError:
        pytest.skip("Port 65535 unavailable for test")

    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=port,
        discovery_auto_retry=True,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    try:
        with pytest.raises(OSError):
            service.start_discovery_server()
        assert service._server is None
        assert service.bound_port is None
        err = capsys.readouterr().err
        assert f"Failed to start OAuth Discovery Server on port {port}" in err
    finally:
        service.stop_discovery_server()
        blocker.close()


def test_discovery_server_invalid_max_retries_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OAUTH_DISCOVERY_MAX_RETRIES", "abc")
    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://auth.example/oauth"],
        scopes_supported=["read"],
        discovery_port=3005,
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    assert service.discovery_max_retries == 50
