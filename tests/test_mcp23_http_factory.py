"""Regression tests for Issue #23: single CORS layer, `resources.subscribe`
agreement between discover and real capability negotiation, era-driven
`/json/version`, and no private `mcp.server.runner` imports in stdio.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp_types import PROTOCOL_VERSION_META_KEY, jsonrpc_message_adapter
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import DIContainer, ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.protocol.version import LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION
from nitrostack.transports.stdio import serve_stdio_streams
from tests.http_probe import decode, http_client, make_app, post

CLIENT_META = {
    "io.modelcontextprotocol/clientInfo": {"name": "issue23-test", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class EchoInput(BaseModel):
    value: str = Field(default="ok")


def _era_app(era: str, name: str):
    @injectable()
    class EchoController:
        @tool(name="echo", description="echo", input_schema=EchoInput)
        async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
            return input.value

    @module(name=f"issue23_{name}", controllers=[EchoController])
    class EchoModule:
        pass

    @mcp_app(module=EchoModule, server=ServerConfig(name=name, protocol_era=era))
    class EchoApp:
        pass

    return asyncio.run(McpApplicationFactory.create(EchoApp))


async def _send(http_app, method: str, path: str, *, headers: dict | None = None, payload: dict | None = None):
    transport = httpx.ASGITransport(app=http_app)
    async with http_app.router.lifespan_context(http_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                headers=headers or {},
                content=json.dumps(payload) if payload is not None else None,
            )


# 1. OPTIONS /mcp: exactly one CORS policy, not two fighting layers.
def test_single_cors_layer_on_options_mcp():
    app = _era_app("auto", "issue23-cors")
    http_app = app.get_combined_app(json_response=True)
    response = asyncio.run(
        _send(
            http_app,
            "OPTIONS",
            "/mcp",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    )
    allow_origin = response.headers.get_list("access-control-allow-origin")
    assert len(allow_origin) == 1


# 2. server/discover and NitroStackMcpServer.get_capabilities must never disagree.
@pytest.mark.parametrize("era", ["legacy", "auto", "modern"])
def test_discover_and_capabilities_agree_on_resources_subscribe(era):
    app = _era_app(era, f"issue23-subscribe-{era}")
    discover = app.handle_server_discover()
    caps = app.mcp_server.get_capabilities()
    assert discover["capabilities"]["resources"]["subscribe"] == caps.resources.subscribe
    assert caps.resources.subscribe == (era == "legacy")
    extra_server = app.create_configured_mcp_server()
    assert extra_server.protocol_era == era
    assert extra_server.get_capabilities().resources.subscribe == (era == "legacy")
    if era == "auto":
        assert app.handle_sessionless_initialize()["capabilities"]["resources"]["subscribe"] is False


# 3. No private `mcp.server.runner` symbol left in stdio.py.
def test_stdio_module_has_no_private_mcp_runner_import():
    source = Path("nitrostack/transports/stdio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == "mcp.server.runner"]
    assert imports
    assert not [alias.name for node in imports for alias in node.names if alias.name.startswith("_")]


# 4. /json/version tracks era instead of a hardcoded 2025 date.
@pytest.mark.parametrize(
    "era, expected_version",
    [
        ("legacy", LEGACY_PROTOCOL_VERSION),
        ("auto", MODERN_PROTOCOL_VERSION),
        ("modern", MODERN_PROTOCOL_VERSION),
    ],
)
def test_json_version_matches_era(era, expected_version):
    app = _era_app(era, f"issue23-jsonversion-{era}")
    http_app = app.get_combined_app(json_response=True)
    response = asyncio.run(_send(http_app, "GET", "/json/version"))
    assert response.status_code == 200
    assert response.json()["Protocol-Version"] == expected_version


# 5. Modern-era stdio boots and serves real requests without the removed
# private import, via the public JSONRPCDispatcher/modern_on_request path.
def test_modern_stdio_serves_tool_call():
    app = _era_app("modern", "issue23-modern-stdio")

    async def roundtrip():
        async with create_client_server_memory_streams() as (client, server):
            client_read, client_write = client
            server_read, server_write = server

            async def run_server():
                await serve_stdio_streams(app.mcp_server, server_read, server_write, "modern")

            async with anyio.create_task_group() as tg:
                tg.start_soon(run_server)
                await client_write.send(
                    SessionMessage(
                        jsonrpc_message_adapter.validate_python(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/call",
                                "params": {
                                    "name": "echo",
                                    "arguments": {"value": "hi"},
                                    "_meta": {PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION, **CLIENT_META},
                                },
                            }
                        )
                    )
                )
                response = await asyncio.wait_for(client_read.receive(), timeout=2)
                tg.cancel_scope.cancel()
            return response.message.model_dump(by_alias=True, mode="json")

    payload = asyncio.run(roundtrip())
    assert "result" in payload
    assert payload["result"]["content"][0]["text"] == "hi"


# 5b. The hand-built on_notify path (no public one-liner exists for it) must
# not crash or hang the dispatcher — proven by a normal request still working
# on the same stream right after a notification.
def test_modern_stdio_notification_does_not_break_the_stream():
    app = _era_app("modern", "issue23-modern-notify")

    async def roundtrip():
        async with create_client_server_memory_streams() as (client, server):
            client_read, client_write = client
            server_read, server_write = server

            async def run_server():
                await serve_stdio_streams(app.mcp_server, server_read, server_write, "modern")

            async with anyio.create_task_group() as tg:
                tg.start_soon(run_server)
                await client_write.send(
                    SessionMessage(
                        jsonrpc_message_adapter.validate_python(
                            {
                                "jsonrpc": "2.0",
                                "method": "notifications/cancelled",
                                "params": {
                                    "requestId": 999,
                                    "_meta": {PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION},
                                },
                            }
                        )
                    )
                )
                await client_write.send(
                    SessionMessage(
                        jsonrpc_message_adapter.validate_python(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {
                                    "name": "echo",
                                    "arguments": {"value": "still-alive"},
                                    "_meta": {PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION, **CLIENT_META},
                                },
                            }
                        )
                    )
                )
                response = await asyncio.wait_for(client_read.receive(), timeout=2)
                tg.cancel_scope.cancel()
            return response.message.model_dump(by_alias=True, mode="json")

    payload = asyncio.run(roundtrip())
    assert payload["result"]["content"][0]["text"] == "still-alive"


async def _stdio_exchange(server, era, messages, expected_responses):
    """Keep one stream alive through handshake, notifications, and requests."""
    async with create_client_server_memory_streams() as (client, streams):
        client_read, client_write = client
        server_read, server_write = streams
        async with anyio.create_task_group() as group:
            group.start_soon(serve_stdio_streams, server, server_read, server_write, era)
            for message in messages:
                await client_write.send(SessionMessage(jsonrpc_message_adapter.validate_python(message)))
            responses = []
            while len(responses) < expected_responses:
                frame = await asyncio.wait_for(client_read.receive(), timeout=3)
                data = frame.message.model_dump(by_alias=True, mode="json")
                if "id" in data:
                    responses.append(data)
            group.cancel_scope.cancel()
        return responses


@pytest.mark.parametrize("era,style", [
    ("legacy", "legacy"), ("auto", "legacy"), ("auto", "modern"), ("modern", "modern"),
])
def test_stdio_era_roundtrip_and_initialization_capabilities(era, style):
    app = make_app(era)
    messages = []
    if style == "legacy":
        messages.extend([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": LEGACY_PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ])
    params = {"name": "echo", "arguments": {"value": "stdio"}}
    if style == "modern":
        params["_meta"] = {PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION, **CLIENT_META}
    messages.append({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
    responses = asyncio.run(_stdio_exchange(app.mcp_server, era, messages, 2 if style == "legacy" else 1))
    if style == "legacy":
        assert responses[0]["result"]["capabilities"]["resources"]["subscribe"] == (era == "legacy")
    assert responses[-1]["result"]["structuredContent"]["value"] == "stdio"


def test_modern_stdio_rejects_initialize_and_then_serves_a_request():
    app = make_app("modern")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LEGACY_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {
            "_meta": {PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION, **CLIENT_META},
        }},
    ]
    responses = asyncio.run(_stdio_exchange(app.mcp_server, "modern", messages, 2))
    assert responses[0]["error"]["code"] == -32601
    assert responses[1]["result"]["tools"][0]["name"] == "echo"


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_http_initialize_respects_era(era):
    with http_client(era) as (_, _, client):
        response = post(client, "initialize", {"protocolVersion": LEGACY_PROTOCOL_VERSION,
                        "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}},
                        modern=False, version=LEGACY_PROTOCOL_VERSION)
        assert response.status_code == 200, response.text
        data = decode(response)
        if era == "modern":
            assert data["error"]["code"] == -32601
        else:
            assert data["result"]["capabilities"]["resources"]["subscribe"] is False
