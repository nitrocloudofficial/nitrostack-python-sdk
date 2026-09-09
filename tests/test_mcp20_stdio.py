"""Official mcp 2.x stdio follows the active protocol era."""

from __future__ import annotations

import asyncio
import os
import sys

import anyio
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp_types import PROTOCOL_VERSION_META_KEY, jsonrpc_message_adapter
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.stdio import serve_stdio_streams

MODERN_META = {
    PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "stdio-test", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


def _session_message(method: str, params: dict, req_id: int = 1) -> SessionMessage:
    return SessionMessage(
        jsonrpc_message_adapter.validate_python(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
    )


def _message_payload(session_message: SessionMessage) -> dict:
    return session_message.message.model_dump(by_alias=True, mode="json")


def _echo_app(era: str):
    @injectable()
    class EchoController:
        @tool(name="echo", description="echo", input_schema=EchoInput)
        async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
            return input.value

    @module(name=f"StdioEra{era.title()}", controllers=[EchoController])
    class EchoModule:
        pass

    @mcp_app(module=EchoModule, server=ServerConfig(name=f"stdio-{era}", protocol_era=era))
    class EchoApp:
        pass

    return asyncio.run(McpApplicationFactory.create(EchoApp))


async def _roundtrip(app, era: str, method: str, params: dict) -> dict:
    async with create_client_server_memory_streams() as (client, server):
        client_read, client_write = client
        server_read, server_write = server

        async def run_server():
            await serve_stdio_streams(app.mcp_server, server_read, server_write, era)

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_server)
            await client_write.send(_session_message(method, params))
            response = await asyncio.wait_for(client_read.receive(), timeout=2)
            tg.cancel_scope.cancel()
        return _message_payload(response)


def test_auto_stdio_calls_tool_without_initialize(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _echo_app("auto")
    payload = asyncio.run(
        _roundtrip(
            app,
            "auto",
            "tools/call",
            {
                "name": "echo",
                "arguments": {"value": "ok"},
                "_meta": MODERN_META,
            },
        )
    )
    assert "result" in payload
    assert payload["result"]["content"][0]["text"] == "ok"


def test_auto_stdio_discovers_without_initialize(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _echo_app("auto")
    payload = asyncio.run(
        _roundtrip(app, "auto", "server/discover", {"_meta": MODERN_META})
    )
    assert "result" in payload
    result = payload["result"]
    assert result.get("protocolVersion") == MODERN_PROTOCOL_VERSION or "supportedVersions" in result
    assert "echo" in app._tools


def test_modern_stdio_rejects_initialize(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _echo_app("modern")
    payload = asyncio.run(
        _roundtrip(
            app,
            "modern",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1"},
            },
        )
    )
    assert "error" in payload
    assert "initialize" in payload["error"]["message"]


def test_legacy_stdio_still_answers_initialize(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _echo_app("legacy")
    payload = asyncio.run(
        _roundtrip(
            app,
            "legacy",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1"},
            },
        )
    )
    assert "result" in payload
    assert payload["result"]["protocolVersion"] == "2025-06-18"
    assert payload["result"]["serverInfo"]["name"] == "stdio-legacy"


def test_dual_http_and_stdio_share_the_same_tools(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _echo_app("auto")
    http_app = app.get_combined_app(json_response=True)
    with TestClient(http_app) as client:
        listed = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/list",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": MODERN_META},
            },
        )
    assert listed.status_code == 200, listed.text
    http_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    stdio_payload = asyncio.run(
        _roundtrip(app, "auto", "tools/list", {"_meta": MODERN_META})
    )
    stdio_names = {tool["name"] for tool in stdio_payload["result"]["tools"]}
    assert http_names == stdio_names == {"echo"}
