"""Deprecated-method policy is the same on every modern entry point."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import mcp.types as types
import pytest
from mcp import MCPError as McpError
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.protocol.deprecated import (
    DEPRECATED_MODERN_METHODS,
    deprecated_method_message,
    rejects_deprecated_method,
)
from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.dispatch import IngressContext, StatelessIngressPipeline
from nitrostack.transports.middleware import StatelessTransportMiddleware

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "legacy-client", "version": "1.0"},
    },
}

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


def _pipeline(era: str) -> StatelessIngressPipeline:
    wire_mode = "reject" if era == "modern" else "stateless"
    return StatelessIngressPipeline(
        IngressContext(
            "srv",
            "1.0.0",
            MODERN_PROTOCOL_VERSION,
            wire_mode=wire_mode,
            protocol_era=era,
        )
    )


def _middleware(era: str) -> StatelessTransportMiddleware:
    return StatelessTransportMiddleware(
        lambda scope, receive, send: None,
        pipeline=_pipeline(era),
    )


def _echo_http_app(era: str):
    @injectable()
    class EchoController:
        @tool(name="echo", description="echo", input_schema=EchoInput)
        async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
            return input.value

    @module(name=f"Deprecated{era.title()}", controllers=[EchoController])
    class EchoModule:
        pass

    @mcp_app(module=EchoModule, server=ServerConfig(name=f"deprecated-{era}", protocol_era=era))
    class EchoApp:
        pass

    app = asyncio.run(McpApplicationFactory.create(EchoApp))
    return app, app.get_combined_app(json_response=True)


def test_initialize_is_not_in_retired_method_table():
    assert "initialize" not in DEPRECATED_MODERN_METHODS
    assert "notifications/initialized" not in DEPRECATED_MODERN_METHODS
    assert deprecated_method_message("initialize") is None


@pytest.mark.parametrize("method", sorted(DEPRECATED_MODERN_METHODS))
def test_retired_methods_error_only_on_modern(method: str):
    assert rejects_deprecated_method(method, "modern") is True
    assert rejects_deprecated_method(method, "auto") is False
    assert rejects_deprecated_method(method, "legacy") is False


@pytest.mark.parametrize("entry", ["pipeline", "replay", "http_post", "http_get"])
def test_modern_initialize_is_method_not_found_on_every_route(entry: str, monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    body = json.dumps(INITIALIZE_BODY).encode()
    expected = "Method not found: initialize"

    if entry == "pipeline":

        async def _run():
            status, resp = await _pipeline("modern").handle_post(body, {})
            assert status == 200
            assert resp["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
            assert resp["error"]["message"] == expected

        asyncio.run(_run())
        return

    if entry == "replay":
        rejected = _middleware("modern")._reject_replay_headers(body, {})
        assert rejected is not None
        status, resp = rejected
        assert status == 200
        assert resp["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
        assert resp["error"]["message"] == expected
        return

    _, http_app = _echo_http_app("modern")
    try:
        if entry == "http_post":
            with TestClient(http_app) as client:
                response = client.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE_BODY)
            assert response.status_code == 200
            assert response.json()["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
            assert response.json()["error"]["message"] == expected
            return
        status, payload = asyncio.run(
            _asgi_json_get(http_app, {**JSON_HEADERS, "Mcp-Method": "initialize"})
        )
        assert status == 200
        assert payload["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
        assert payload["error"]["message"] == expected
    finally:
        DIContainer.reset()


async def _asgi_json_get(app, headers: dict[str, str]) -> tuple[int, dict]:
    status = 0
    chunks: list[bytes] = []
    sent_request = False
    finished = asyncio.Event()

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body") or b"")
            if not message.get("more_body", False):
                finished.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": ("testclient", 50000),
        "server": ("test", 80),
    }

    async def _http():
        await app(scope, receive, send)
        await asyncio.wait_for(finished.wait(), timeout=2)
        return status, json.loads(b"".join(chunks) or b"{}")

    started = asyncio.Event()
    messages: asyncio.Queue = asyncio.Queue()
    await messages.put({"type": "lifespan.startup"})

    async def life_receive():
        return await messages.get()

    async def life_send(message):
        if message["type"] == "lifespan.startup.complete":
            started.set()

    task = asyncio.create_task(app({"type": "lifespan"}, life_receive, life_send))
    await asyncio.wait_for(started.wait(), timeout=2)
    try:
        return await _http()
    finally:
        await messages.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.parametrize("entry", ["pipeline", "replay", "http_post"])
def test_auto_initialize_is_not_the_deprecated_error(entry: str, monkeypatch):
    monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    body = json.dumps(INITIALIZE_BODY).encode()

    if entry == "pipeline":

        async def _run():
            status, resp = await _pipeline("auto").handle_post(body, {})
            assert status == 200
            assert "error" not in resp
            assert resp["result"]["protocolVersion"] == "2025-06-18"

        asyncio.run(_run())
        return

    if entry == "replay":
        rejected = _middleware("auto")._reject_replay_headers(body, {})
        assert rejected is None
        return

    _, http_app = _echo_http_app("auto")
    try:
        with TestClient(http_app) as client:
            response = client.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE_BODY)
        assert response.status_code == 200
        payload = response.json()
        assert "error" not in payload
        assert payload["result"]["protocolVersion"] == "2025-06-18"
    finally:
        DIContainer.reset()
        os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


@pytest.mark.parametrize("method", sorted(DEPRECATED_MODERN_METHODS))
@pytest.mark.parametrize("entry", ["pipeline", "replay"])
def test_modern_retired_methods_match_on_post_and_replay(method: str, entry: str):
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": {}}).encode()
    headers = {"Mcp-Method": method}
    expected = deprecated_method_message(method)

    async def _pipeline_reject():
        status, resp = await _pipeline("modern").handle_post(body, headers)
        assert status == 200
        assert resp["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
        assert resp["error"]["message"] == expected

    if entry == "pipeline":
        asyncio.run(_pipeline_reject())
        return

    rejected = _middleware("modern")._reject_replay_headers(body, headers)
    assert rejected is not None
    status, resp = rejected
    assert status == 200
    assert resp["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)
    assert resp["error"]["message"] == expected


def test_auto_official_handler_still_lists_tasks(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app, _ = _echo_http_app("auto")
    try:
        handler = app.mcp_server.request_handlers[types.ListTasksRequest]
        result = asyncio.run(handler(types.ListTasksRequest(method="tasks/list", params={})))
        assert result.tasks == []
    finally:
        DIContainer.reset()


def test_modern_official_handler_rejects_tasks_list(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app, _ = _echo_http_app("modern")
    try:
        handler = app.mcp_server.request_handlers[types.ListTasksRequest]
        with pytest.raises(McpError) as exc:
            asyncio.run(handler(types.ListTasksRequest(method="tasks/list", params={})))
        assert exc.value.error.code == types.METHOD_NOT_FOUND
        assert exc.value.error.message == deprecated_method_message("tasks/list")
    finally:
        DIContainer.reset()
