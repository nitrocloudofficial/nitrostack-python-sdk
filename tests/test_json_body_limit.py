"""Configurable JSON body limit on HTTP transports (TS #366)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import BaseModel

from nitrostack import McpApplicationFactory, ServerConfig, injectable, mcp_app, module, tool
from nitrostack.core.context import ExecutionContext
from nitrostack.transports.http import build_http_app
from nitrostack.transports.http.middleware import JsonBodyLimitMiddleware

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}


class EmptyInput(BaseModel):
    pass


@injectable()
class PingController:
    @tool(name="ping", description="ping", input_schema=EmptyInput)
    async def ping(self, input: EmptyInput, context: ExecutionContext) -> str:
        return "pong"


@module(name="ping", controllers=[PingController])
class PingModule:
    pass


async def _app():
    @mcp_app(module=PingModule, server=ServerConfig(name="body-limit-test", protocol_era="legacy"))
    class TestApp:
        pass

    return await McpApplicationFactory.create(TestApp)


def _big_initialize(padding: str) -> dict:
    body = dict(INITIALIZE)
    body["params"] = dict(body["params"])
    body["params"]["clientInfo"] = dict(body["params"]["clientInfo"])
    body["params"]["clientInfo"]["padding"] = padding
    return body


def _json_post_scope(content_type: bytes = b"application/json") -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"spec_version": "2.4", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", content_type)],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }


def test_json_body_limit_rejects_oversize_with_413():
    app = asyncio.run(_app())
    http_app = build_http_app(
        app,
        enable_cors=True,
        protocol_era="legacy",
        json_body_limit="1kb",
    )
    big = _big_initialize("x" * 2048)
    with TestClient(http_app) as client:
        assert client.post("/mcp", json=big).status_code == 413
        assert client.post("/mcp", json=INITIALIZE).status_code == 200


def test_json_body_limit_default_accepts_over_1kb():
    app = asyncio.run(_app())
    http_app = build_http_app(app, enable_cors=True, protocol_era="legacy")
    big = _big_initialize("x" * 2048)
    with TestClient(http_app) as client:
        assert client.post("/mcp", json=big).status_code == 200


def test_json_body_limit_default_rejects_over_1mb():
    app = asyncio.run(_app())
    http_app = build_http_app(app, enable_cors=True, protocol_era="legacy")
    big = _big_initialize("x" * (1024 * 1024 + 512))
    with TestClient(http_app) as client:
        assert client.post("/mcp", json=big).status_code == 413


@pytest.mark.asyncio
async def test_json_body_limit_chunked_rejects_with_single_413(caplog):
    app_calls = 0

    async def inner_app(scope, receive, send):
        nonlocal app_calls
        app_calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    limit = 200
    middleware = JsonBodyLimitMiddleware(inner_app, limit_bytes=limit)
    body = b"x" * (limit + 50)
    chunks = [body[:100], body[100:]]
    chunk_index = 0

    async def receive():
        nonlocal chunk_index
        if chunk_index < len(chunks):
            message = {
                "type": "http.request",
                "body": chunks[chunk_index],
                "more_body": chunk_index < len(chunks) - 1,
            }
            chunk_index += 1
            return message
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def capture_send(message):
        sent.append(message)

    with caplog.at_level("ERROR"):
        await middleware(_json_post_scope(), receive, capture_send)

    assert app_calls == 0
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    assert not any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.asyncio
async def test_json_body_limit_chunked_within_limit_reaches_app():
    app_calls = 0

    async def inner_app(scope, receive, send):
        nonlocal app_calls
        app_calls += 1
        chunks: list[bytes] = []
        while True:
            message = await receive()
            assert message["type"] == "http.request"
            chunks.append(message.get("body", b"") or b"")
            if not message.get("more_body", False):
                break
        assert json.loads(b"".join(chunks)) == {"ok": True}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    limit = 512
    middleware = JsonBodyLimitMiddleware(inner_app, limit_bytes=limit)
    payload = json.dumps({"ok": True}).encode("utf-8")
    chunks = [payload[:20], payload[20:]]
    chunk_index = 0

    async def receive():
        nonlocal chunk_index
        if chunk_index < len(chunks):
            message = {
                "type": "http.request",
                "body": chunks[chunk_index],
                "more_body": chunk_index < len(chunks) - 1,
            }
            chunk_index += 1
            return message
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def capture_send(message):
        sent.append(message)

    content_type = b"application/vnd.api+json; charset=utf-8"
    await middleware(_json_post_scope(content_type), receive, capture_send)

    assert app_calls == 1
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 200