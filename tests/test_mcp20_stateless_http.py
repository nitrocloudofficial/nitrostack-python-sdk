"""Tests for MCP 2.0 stateless HTTP transport."""

import asyncio
import json

import pytest

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.contracts import MCP_CACHE_HINT_KEY
from nitrostack.protocol.discovery import DISCOVER_RESULT_TYPE, build_discover_result
from nitrostack.protocol.jsonrpc import (
    HEADER_BODY_MISMATCH,
    PARSE_ERROR,
    HeaderBodyMismatchError,
    JsonRpcParseError,
    build_ping_response,
    parse_jsonrpc_request,
    validate_header_body_method,
)
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.cors import build_cors_headers, cors_preflight_response_headers, resolve_allowed_origin
from nitrostack.transports.dispatch import (
    IngressContext,
    StatelessIngressPipeline,
    is_task_wire_interception,
)
from nitrostack.transports.headers import (
    build_mcp_response_headers,
    build_sse_stream_headers,
    strip_legacy_session_headers,
)
from nitrostack.transports.sse import format_sse_message, sse_notification


class TestRequestHeaders:
    def test_strip_legacy_session_header(self):
        headers = {"Mcp-Session-Id": "legacy", "Content-Type": "application/json"}
        cleaned = strip_legacy_session_headers(headers)
        assert LEGACY_SESSION_HEADER not in cleaned
        assert cleaned["Content-Type"] == "application/json"

    def test_response_headers_include_protocol_version(self):
        headers = build_mcp_response_headers()
        assert headers["MCP-Protocol-Version"] == MODERN_PROTOCOL_VERSION
        assert headers["Vary"] == "Origin"


class TestCors:
    def test_cors_allow_methods(self):
        headers = build_cors_headers()
        assert "GET, POST, DELETE, OPTIONS" in headers["Access-Control-Allow-Methods"]
        assert "Mcp-Method" in headers["Access-Control-Allow-Headers"]

    def test_preflight_headers(self):
        headers = cors_preflight_response_headers({"Origin": "https://app.example.com"})
        assert "Access-Control-Allow-Origin" in headers

    def test_does_not_reflect_arbitrary_origin(self):
        headers = build_cors_headers(origin="https://evil.example")
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_allowlist_echoes_only_listed_origin(self):
        assert resolve_allowed_origin(
            "https://app.example.com",
            allowed_origins=("https://app.example.com",),
        ) == "https://app.example.com"
        assert resolve_allowed_origin(
            "https://evil.example",
            allowed_origins=("https://app.example.com",),
        ) == "https://app.example.com"


class TestJsonRpcParsing:
    def test_parse_valid_request(self):
        body = json.dumps(
            {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": "x"}}
        ).encode()
        req = parse_jsonrpc_request(body)
        assert req.method == "tools/call"
        assert req.id == "1"

    def test_parse_invalid_json(self):
        with pytest.raises(JsonRpcParseError) as exc:
            parse_jsonrpc_request(b"{bad")
        assert exc.value.code == PARSE_ERROR

    def test_header_body_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_header_body_method("tools/list", "tools/call")
        assert exc.value.code == HEADER_BODY_MISMATCH


class TestPingFastPath:
    def test_ping_response(self):
        resp = build_ping_response("ping-1")
        assert resp == {"jsonrpc": "2.0", "id": "ping-1", "result": {}}


class TestDiscovery:
    def test_discover_result_shape(self):
        result = build_discover_result(
            server_name="my-stateless-mcp-server",
            server_version="1.0.0",
        )
        assert result["protocolVersion"] == "2026-07-28"
        assert result["supportedVersions"] == ["2026-07-28"]
        assert result["serverInfo"]["name"] == "my-stateless-mcp-server"
        assert result["capabilities"]["resources"]["subscribe"] is False
        assert "io.modelcontextprotocol/tasks" in result["capabilities"]["extensions"]
        assert result["resultType"] == DISCOVER_RESULT_TYPE
        assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
        assert result["cacheScope"] in ("public", "private")
        assert result["_meta"][MCP_CACHE_HINT_KEY]["ttlMs"] == result["ttlMs"]
        assert result["_meta"][MCP_CACHE_HINT_KEY]["cacheScope"] == result["cacheScope"]


class TestDispatchPipeline:
    def test_handles_ping(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            assert resp["result"] == {}

        asyncio.run(_run())

    def test_handles_server_discover(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps(
                {"jsonrpc": "2.0", "id": "req-001", "method": "server/discover", "params": {}}
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            result = resp["result"]
            assert result["protocolVersion"] == "2026-07-28"
            assert result["resultType"] == DISCOVER_RESULT_TYPE
            assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
            assert result["cacheScope"] in ("public", "private")
            assert MODERN_PROTOCOL_VERSION in result["supportedVersions"]

        asyncio.run(_run())

    def test_passes_through_tools_call(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "demo"},
                }
            ).encode()
            assert await pipeline.handle_post(body, {}) is None

        asyncio.run(_run())


class TestTaskInterceptionDetection:
    def test_tasks_method_prefix(self):
        assert is_task_wire_interception("tasks/get", {"taskId": "x"})

    def test_tools_call_with_task_param(self):
        assert is_task_wire_interception("tools/call", {"name": "x", "task": {"ttl": 1000}})

    def test_sync_tools_call_not_intercepted(self):
        assert not is_task_wire_interception("tools/call", {"name": "x"})


class TestSse:
    def test_sse_stream_headers_disable_buffering(self):
        headers = build_sse_stream_headers()
        assert headers["X-Accel-Buffering"] == "no"
        assert headers["Cache-Control"] == "no-transform"
        assert headers["Content-Type"] == "text/event-stream"

    def test_sse_notification_format(self):
        frame = sse_notification("notifications/tools/list_changed")
        text = frame.decode()
        assert text.startswith("event: message")
        assert "notifications/tools/list_changed" in text

    def test_sse_task_status_notification(self):
        frame = format_sse_message(
            {"method": "notifications/tasks/status", "params": {"taskId": "abc"}}
        )
        assert b"notifications/tasks/status" in frame


class TestReplayDoesNotSynthesizeDisconnect:
    def test_waits_for_real_disconnect(self):
        from nitrostack.transports.middleware import StatelessTransportMiddleware

        calls = {"n": 0}

        async def original_receive():
            calls["n"] += 1
            return {"type": "http.disconnect"}

        replay = StatelessTransportMiddleware._replay_receive(b"{}", original_receive)

        async def _run():
            first = await replay()
            assert first == {"type": "http.request", "body": b"{}", "more_body": False}
            assert calls["n"] == 0
            second = await replay()
            assert second == {"type": "http.disconnect"}
            assert calls["n"] == 1

        asyncio.run(_run())


class TestOptionsScopedToMcpPath:
    def test_options_mcp_is_204_health_is_forwarded(self):
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer
        from pydantic import BaseModel, Field

        class EchoInput(BaseModel):
            value: str = Field(default="")

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="OptionsHttp", controllers=[EchoController])
            class OptionsModule:
                pass

            @mcp_app(module=OptionsModule, server=ServerConfig(name="options-http", stateless=True))
            class OptionsApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(OptionsApp))
            http_app = app.get_combined_app(stateless=True, json_response=True)
            with TestClient(http_app) as client:
                mcp_opt = client.options("/mcp")
                health_opt = client.options("/mcp/health")
                call = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
            assert mcp_opt.status_code == 204
            assert health_opt.status_code != 204
            assert call.status_code == 200
            assert call.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()


class TestProviderToolDiscovery:
    def test_provider_tools_are_registered(self):
        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.decorators import ToolConfig
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        assert ToolConfig(name="x", description="d", input_schema={}).task_support == "forbidden"

        DIContainer.reset()
        try:
            @injectable()
            class ToolProvider:
                @tool(name="from_provider", description="provider", input_schema=EchoInput)
                async def from_provider(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="ProviderTools", providers=[ToolProvider], controllers=[])
            class ProviderModule:
                pass

            @mcp_app(module=ProviderModule, server=ServerConfig(name="provider-tools"))
            class ProviderApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(ProviderApp))
            assert "from_provider" in app._tools
            assert app._tools["from_provider"].config.task_support == "forbidden"
        finally:
            DIContainer.reset()
