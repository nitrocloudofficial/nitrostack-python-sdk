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


def test_official_mcp_ping_is_method_not_found_without_a_default_session_id():
    response = _request(
        "/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert response.status_code == 200
    assert response.headers.get("mcp-session-id") is None
    body = response.json()
    assert body["error"]["code"] == -32601
    assert "result" not in body


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


# ---------------------------------------------------------------------------
# Request context, transforms and CORS on the official /mcp path
# ---------------------------------------------------------------------------

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

from nitrostack import ExecutionContext, injectable, tool
from nitrostack.core.di import DIContainer
from nitrostack.runtime.caller import caller_identity
from nitrostack.tasks.authorization import extract_task_access_context
from nitrostack.transforms import McpTransformBase
from nitrostack.transforms.search import RegexSearchTransform

_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class _EchoInput(BaseModel):
    value: str = Field(default="")


class _Recorder(McpTransformBase):
    name = "recorder"

    def __init__(self):
        self.seen = []

    async def transform_tools(self, tools, ctx):
        self.seen.append(ctx)
        return list(tools)


def _build(era: str, transforms=None):
    DIContainer.reset()

    @injectable()
    class EchoController:
        @tool(name="echo", description="echo", input_schema=_EchoInput)
        async def echo(self, input: _EchoInput, context: ExecutionContext) -> str:
            return input.value

    @module(name=f"owner_{era}", controllers=[EchoController])
    class OwnerModule:
        pass

    @mcp_app(
        module=OwnerModule,
        server=ServerConfig(name=f"owner-{era}", protocol_era=era),
        transforms=transforms,
    )
    class OwnerApp:
        pass

    application = asyncio.run(McpApplicationFactory.create(OwnerApp))
    return application.get_combined_app(json_response=True)


def _rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def test_transform_context_is_populated_over_http_for_tools_list():
    recorder = _Recorder()
    app = _build("auto", transforms=[recorder])
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers=_HEADERS,
            json=_rpc("tools/list", {"_meta": {"userId": "mallory"}}),
        )
    assert response.status_code == 200
    ctx = recorder.seen[-1]
    assert ctx.execution_context is not None
    assert ctx.execution_context.request_id
    assert ctx.session_id is None  # sessionless era mints no durable session
    assert ctx.subject is None  # unsigned _meta.userId is never identity


def test_regex_search_transform_works_end_to_end_over_http():
    app = _build("auto", transforms=[RegexSearchTransform()])
    with TestClient(app) as client:
        listed = client.post("/mcp", headers=_HEADERS, json=_rpc("tools/list"))
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert {"search_tools", "call_tool"} <= names
        assert "echo" not in names

        called = client.post(
            "/mcp",
            headers=_HEADERS,
            json=_rpc(
                "tools/call",
                {"name": "call_tool", "arguments": {"name": "echo", "arguments": {"value": "hi"}}},
                id_=2,
            ),
        )
    assert called.status_code == 200
    assert "hi" in json.dumps(called.json()["result"])


def test_legacy_era_transform_context_carries_the_session_id():
    recorder = _Recorder()
    app = _build("legacy", transforms=[recorder])
    with TestClient(app) as client:
        init = client.post(
            "/mcp",
            headers=_HEADERS,
            json=_rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
        )
        session = init.headers["mcp-session-id"]
        headers = {**_HEADERS, "mcp-session-id": session, "mcp-protocol-version": "2025-06-18"}
        client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        client.post("/mcp", headers=headers, json=_rpc("tools/list", id_=2))
    assert recorder.seen[-1].session_id == session


def test_modern_era_rejects_the_2025_initialize_on_the_official_path():
    app = _build("modern")
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers=_HEADERS,
            json=_rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            ),
        )
    body = response.json()
    assert body["error"]["code"] == -32601
    assert body["error"]["message"] == "Method not found: initialize"
    assert "mcp-session-id" not in response.headers


def test_cors_honours_the_configured_origin_allowlist(monkeypatch):
    monkeypatch.setenv("MCP_CORS_ALLOWED_ORIGINS", "https://app.example.com")
    app = _build("auto")
    preflight_headers = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, mcp-param-city",
    }
    with TestClient(app) as client:
        allowed = client.options(
            "/mcp", headers={**preflight_headers, "Origin": "https://app.example.com"}
        )
        denied = client.options(
            "/mcp", headers={**preflight_headers, "Origin": "https://evil.example.com"}
        )
        post = client.post(
            "/mcp",
            headers={**_HEADERS, "Origin": "https://app.example.com"},
            json=_rpc("ping"),
        )
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "mcp-param-city" in allowed.headers["access-control-allow-headers"].lower()
    assert denied.headers.get("access-control-allow-origin") != "*"
    assert denied.headers.get("access-control-allow-origin") != "https://evil.example.com"
    assert post.headers["access-control-allow-origin"] == "https://app.example.com"
    expose = post.headers["access-control-expose-headers"]
    assert "Mcp-Method" in expose and "Mcp-Name" in expose


def test_cors_exposes_session_id_only_on_the_legacy_wire(monkeypatch):
    monkeypatch.delenv("MCP_CORS_ALLOWED_ORIGINS", raising=False)
    for era, exposed in (("legacy", True), ("auto", False)):
        app = _build(era)
        with TestClient(app) as client:
            response = client.options(
                "/mcp",
                headers={"Origin": "http://x.test", "Access-Control-Request-Method": "POST"},
            )
        expose = response.headers.get("access-control-expose-headers", "")
        assert ("Mcp-Session-Id" in expose) is exposed, era


def test_caller_identity_ignores_session_on_sessionless_eras():
    rc = SimpleNamespace(session=SimpleNamespace(id="sess-1"))
    assert caller_identity(rc, "legacy") == ("sess-1", None)
    assert caller_identity(rc, "auto") == (None, None)
    assert caller_identity(rc, "modern") == (None, None)
    assert caller_identity(None, "legacy") == (None, None)


def test_task_access_uses_the_same_session_rule_as_transforms():
    rc = SimpleNamespace(session=SimpleNamespace(id="sess-1"))
    assert extract_task_access_context(rc, "legacy").session_id == "sess-1"
    assert extract_task_access_context(rc, "auto").session_id is None
