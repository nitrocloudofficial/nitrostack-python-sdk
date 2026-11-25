"""Configurable JSON body limit on HTTP transports (TS #366)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import BaseModel

from nitrostack import McpApplicationFactory, ServerConfig, injectable, mcp_app, module, tool
from nitrostack.core.context import ExecutionContext
from nitrostack.transports.http import build_http_app


class EmptyInput(BaseModel):
    pass

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