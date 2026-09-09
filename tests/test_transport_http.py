"""Phase 6 — extra HTTP transport cases (Python-only)."""
from __future__ import annotations

import asyncio
import time
import os
import sys

from pydantic import BaseModel
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import DIContainer, ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.transports.http import RequestTraceMiddleware, SessionCapMiddleware, build_http_app

JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class EchoIn(BaseModel):
    value: str = "x"


@injectable()
class HttpExtraController:
    @tool(name="echo", description="echo", input_schema=EchoIn)
    async def echo(self, input: EchoIn, context: ExecutionContext) -> str:
        return input.value


@module(name="http-extra", controllers=[HttpExtraController])
class HttpExtraModule:
    pass


def _app(name: str = "http-extra"):
    @mcp_app(module=HttpExtraModule, server=ServerConfig(name=name, stateless=False))
    class App:
        pass

    return asyncio.run(McpApplicationFactory.create(App))


def test_landing_html_escapes_server_name():
    app = _app(name='<script>alert("xss")</script>')
    http_app = build_http_app(app, enable_cors=True, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        page = client.get("/")
    assert page.status_code == 200
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_cors_disabled_rejects_disallowed_origin(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://allowed.example")
    app = _app()
    http_app = build_http_app(app, enable_cors=False, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        resp = client.post(
            "/mcp",
            headers={**JSON_HEADERS, "Origin": "http://evil.example"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        )
    assert resp.status_code in {400, 403, 421}


def test_stateful_tool_call_without_session_is_rejected():
    app = _app()
    http_app = build_http_app(
        app,
        enable_cors=True,
        protocol_era="legacy",
        wire_mode="sessionful",
        json_response=True,
    )
    with TestClient(http_app) as client:
        resp = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"value": "no-session"}},
            },
        )
    assert resp.status_code == 400



def test_request_trace_redacts_authorization_header():
    decoded = RequestTraceMiddleware._decode_headers(
        [(b"Authorization", b"Bearer secret"), (b"Accept", b"application/json")]
    )
    assert decoded["Authorization"] == "<redacted>"
    assert decoded["Accept"] == "application/json"


def test_http_debug_middleware_logs_request(monkeypatch, capsys):
    monkeypatch.setenv("NITROSTACK_HTTP_DEBUG", "1")
    app = _app()
    http_app = build_http_app(app, enable_cors=True, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        client.get("/mcp/health")
    captured = capsys.readouterr()
    joined = captured.err + captured.out
    assert "trace" in joined.lower()
    assert "/mcp/health" in joined


def test_session_cap_prunes_idle_sessions():
    async def dummy_app(scope, receive, send):
        return None

    cap = SessionCapMiddleware(dummy_app, max_sessions=2, session_idle_timeout=0.05)
    cap._sessions["stale"] = time.monotonic() - 10
    cap._sessions["fresh"] = time.monotonic()
    cap._prune_stale()
    assert "stale" not in cap._sessions
    assert "fresh" in cap._sessions
    assert cap.active_session_count == 1
