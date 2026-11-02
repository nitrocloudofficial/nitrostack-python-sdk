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


def test_auto_official_handler_hides_tasks_list(monkeypatch):
    # Tasks are hidden on the 2026 wire (auto included), so there is no handler.
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app, _ = _echo_http_app("auto")
    try:
        assert types.ListTasksRequest not in app.mcp_server.request_handlers
    finally:
        DIContainer.reset()


def test_modern_official_handler_hides_tasks_list(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app, _ = _echo_http_app("modern")
    try:
        assert types.ListTasksRequest not in app.mcp_server.request_handlers
    finally:
        DIContainer.reset()
