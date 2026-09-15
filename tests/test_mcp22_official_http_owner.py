"""Regression tests for Issue #22: one official owner for ``/mcp``."""

import asyncio
import json
from pathlib import Path

import httpx

from nitrostack import module
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app


@module(name="issue22_probe", controllers=[])
class Issue22ProbeModule:
    pass


@mcp_app(
    module=Issue22ProbeModule,
    server=ServerConfig(name="issue22-probe", protocol_era="auto"),
)
class Issue22ProbeApp:
    pass


def _request(path: str, *, method: str = "GET", payload: dict | None = None):
    async def run():
        application = await McpApplicationFactory.create(Issue22ProbeApp)
        http_app = application.get_combined_app(json_response=True)
        transport = httpx.ASGITransport(app=http_app)
        async with http_app.router.lifespan_context(http_app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(
                    method,
                    path,
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                    },
                    content=json.dumps(payload) if payload is not None else None,
                )

    return asyncio.run(run())


def test_combined_app_has_no_jsonrpc_sidecar_call():
    source = Path("nitrostack/core/app.py").read_text(encoding="utf-8")
    assert "wrap_stateless_transport" not in source
    assert "return http_app" in source


def test_sidecar_modules_and_ping_helpers_are_not_production_code():
    assert not Path("nitrostack/transports/middleware.py").exists()
    assert not Path("nitrostack/transports/dispatch.py").exists()
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("nitrostack").rglob("*.py")
    )
    for symbol in (
        "wrap_stateless_transport",
        "wrap_sessionless_http",
        "wrap_modern_handshake_reject",
        "StatelessIngressPipeline",
        "build_ping_response",
        "is_header_only_ping",
    ):
        assert symbol not in production


def test_health_endpoint_remains_available():
    response = _request("/mcp/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_official_mcp_handles_ping_without_a_default_session_id():
    response = _request(
        "/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert response.status_code == 200
    assert response.headers.get("mcp-session-id") is None
    assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_official_mcp_returns_jsonrpc_error_for_unknown_method():
    response = _request(
        "/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 2, "method": "issue22/unknown"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 2
    assert body["error"]["code"] == -32601
    assert "Method not found" in body["error"]["message"]
