"""Issue #24 wire behavior through the official MCP mount and public APIs."""

import asyncio
import os
from unittest.mock import patch

import mcp.types as types
from mcp import MCPError
import pytest
from pydantic import BaseModel

from nitrostack import DIContainer, module, prompt, resource, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.protocol.contracts import MCP_CACHE_HINT_KEY
from nitrostack.protocol.errors import ERROR_CODE_MESSAGES, JsonRpcErrorCode
from nitrostack.protocol.version import LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION
from nitrostack.transports.http import HeaderCompatMiddleware
from tests.http_probe import decode, envelope, headers, http_client, make_app, post


class EchoInput(BaseModel):
    value: str = "ok"


def wire_app(era):
    DIContainer.reset()
    class Operations:
        @tool(name="echo", description="echo", input_schema=EchoInput, task_support="optional")
        async def echo(self, input, context):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=input.value)],
                _meta={"application/example": "preserved"},
            )

        @resource(uri="wire://info", name="info", description="info")
        async def info(self, context):
            return "wire resource"

        @resource(uri="wire://items/{item}", name="item", description="item template")
        async def item(self, item, context):
            return item

        @prompt(name="greet", description="greet")
        async def greet(self, arguments, context):
            return [{"role": "user", "content": "hello"}]

    @module(name="wire-operations", providers=[Operations])
    class WireModule:
        pass

    @mcp_app(module=WireModule, server=ServerConfig(name="wire-test", version="1.2.3", protocol_era=era))
    class WireApp:
        pass

    with patch.dict(os.environ, {"NITRO_MCP_PROTOCOL_VERSION": era, "MCP_STATELESS": ""}):
        return asyncio.run(McpApplicationFactory.create(WireApp))


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("method,params", [
    ("ping", {}), ("initialize", {}), ("tasks/list", {}),
    ("tasks/result", {"taskId": "missing"}),
    ("resources/subscribe", {"uri": "probe://info"}),
    ("logging/setLevel", {"level": "info"}),
])
def test_retired_methods_on_the_modern_wire(era, method, params):
    with http_client(era) as (_, _, client):
        response = post(client, method, params)
        assert response.status_code == 404, response.text
        assert decode(response)["error"]["code"] == -32601


def test_auto_legacy_shaped_ping_is_also_retired():
    with http_client("auto") as (_, _, client):
        response = client.post("/mcp", json=envelope("ping", modern=False),
                               headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 200
        assert decode(response)["error"]["code"] == -32601


def test_legacy_ping_still_works_after_initialization():
    with http_client("legacy") as (_, _, client):
        response = post(client, "ping", modern=False, version=LEGACY_PROTOCOL_VERSION)
        assert response.status_code == 200
        assert decode(response)["result"] == {}


@pytest.mark.parametrize("method,params", [
    ("server/discover", {}), ("tools/list", {}),
    ("tools/call", {"name": "echo", "arguments": {}}),
    ("resources/read", {"uri": "probe://info"}),
])
@pytest.mark.parametrize("body_has_modern_meta", [True, False])
@pytest.mark.parametrize("version", [None, "", "   "])
def test_modern_requests_without_a_version_header_fail_closed(method, params, body_has_modern_meta, version):
    with http_client("modern") as (_, _, client):
        outgoing = headers(method)
        outgoing.pop("MCP-Protocol-Version")
        if version is not None:
            outgoing["MCP-Protocol-Version"] = version
        response = client.post("/mcp", headers=outgoing,
                               json=envelope(method, params, modern=body_has_modern_meta))
        assert response.status_code == 400, response.text
        body = decode(response)
        assert body["error"]["code"] == -32020
        assert "result" not in body


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("method,params,name", [
    ("tools/call", {"name": "echo", "arguments": {}}, "echo"),
    ("resources/read", {"uri": "probe://info"}, "probe://info"),
    ("prompts/get", {"name": "greet"}, "greet"),
])
@pytest.mark.parametrize("fault", ["missing-method", "wrong-method", "missing-name", "wrong-name"])
def test_official_transport_enforces_method_and_name_headers(era, method, params, name, fault):
    with http_client(era) as (_, _, client):
        outgoing = headers(method, name=name)
        if fault == "missing-method":
            outgoing.pop("Mcp-Method")
        elif fault == "wrong-method":
            outgoing["Mcp-Method"] = "tools/list"
        elif fault == "missing-name":
            outgoing.pop("Mcp-Name")
        else:
            outgoing["Mcp-Name"] = "wrong"
        response = client.post("/mcp", headers=outgoing, json=envelope(method, params))
        assert response.status_code == 400, response.text
        assert decode(response)["error"]["code"] == -32020


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("method,params", [
    ("tools/list", {}), ("tools/call", {"name": "echo", "arguments": {"value": "hello"}}),
    ("resources/list", {}), ("resources/templates/list", {}),
    ("resources/read", {"uri": "wire://info"}),
    ("prompts/list", {}), ("prompts/get", {"name": "greet"}),
])
@pytest.mark.parametrize("json_response", [True, False])
def test_complete_results_use_native_result_type_and_preserve_metadata(era, method, params, json_response):
    with http_client(era, app=wire_app(era), json_response=json_response) as (_, _, client):
        response = post(client, method, params)
        assert response.status_code == 200, response.text
        result = decode(response)["result"]
        assert result["resultType"] == "complete"
        assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "wire-test"
        if method == "tools/call":
            assert result["content"][0]["text"] == "hello"
            assert result["_meta"]["application/example"] == "preserved"
        if method in ("tools/list", "resources/list", "prompts/list"):
            assert MCP_CACHE_HINT_KEY in result["_meta"]
        if method == "resources/templates/list":
            assert result["resourceTemplates"][0]["uriTemplate"] == "wire://items/{item}"
        assert "mcp-session-id" not in response.headers


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_discovery_uses_one_builder_and_does_not_advertise_unimplemented_tasks(era):
    app = wire_app(era)
    direct = app.handle_server_discover()
    assert direct["_meta"]["serverInfo"] == {"name": "wire-test", "version": "1.2.3"}
    assert "io.modelcontextprotocol/tasks" not in direct["capabilities"].get("extensions", {})
    with http_client(era, app=app) as (_, _, client):
        response = post(client, "server/discover")
        result = decode(response)["result"]
        assert result["_meta"]["serverInfo"] == direct["_meta"]["serverInfo"]
        assert result["resultType"] == "complete"
        assert "io.modelcontextprotocol/tasks" not in result["capabilities"].get("extensions", {})


def test_auto_still_accepts_legacy_initialization_and_tool_calls():
    with http_client("auto") as (_, _, client):
        initialized = post(client, "initialize", {
            "protocolVersion": LEGACY_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "legacy", "version": "1"},
        }, modern=False, version=LEGACY_PROTOCOL_VERSION)
        assert "result" in decode(initialized)
        called = post(client, "tools/call", {"name": "echo", "arguments": {"value": "legacy"}},
                      modern=False, version=LEGACY_PROTOCOL_VERSION)
        assert decode(called)["result"]["structuredContent"]["value"] == "legacy"


def test_missing_client_capability_code_is_public_for_the_tasks_followup():
    # Definition/export only: live capability failures belong to the tasks issue.
    from nitrostack.protocol import JsonRpcErrorCode as PublicCodes
    code = PublicCodes.MISSING_REQUIRED_CLIENT_CAPABILITY
    assert int(code) == -32021
    assert ERROR_CODE_MESSAGES[code] == "Missing required client capability"


def test_header_rejection_never_reads_or_dispatches_the_body():
    async def scenario():
        async def forbidden(*args):
            raise AssertionError("A missing-version rejection must not read or dispatch JSON-RPC")
        sent = []
        async def send(message):
            sent.append(message)
        await HeaderCompatMiddleware(forbidden, require_protocol_version=True)(
            {"type": "http", "method": "POST", "headers": []}, forbidden, send,
        )
        assert sent[0]["status"] == 400
    asyncio.run(scenario())


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_duplicate_version_headers_reach_official_rejection(era):
    with http_client(era) as (_, _, client):
        outgoing = list(headers().items()) + [("MCP-Protocol-Version", LEGACY_PROTOCOL_VERSION)]
        response = client.post("/mcp", headers=outgoing, json=envelope())
        assert response.status_code == 400, response.text
        assert "result" not in decode(response)


def test_auto_modern_envelope_cannot_bypass_version_header_requirement():
    with http_client("auto") as (_, _, client):
        response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                               json=envelope())
        assert decode(response)["error"]["code"] == -32020


def test_fresh_server_and_mrtr_results_have_correct_policy_before_http_startup():
    app = make_app("modern")
    try:
        server = app.create_configured_mcp_server()
        assert isinstance(server, NitroStackMcpServer) and server.protocol_era == "modern"
        with pytest.raises(MCPError) as exc:
            asyncio.run(server._handle_ping(None, None))
        assert exc.value.error.code == -32601
        result = app._to_call_tool_result(types.CallToolResult(content=[], resultType="input_required"))
        assert result.result_type == "input_required"
    finally:
        DIContainer.reset()
