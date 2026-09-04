"""Tests for MCP 2.0 stateless HTTP transport (Doc 01)."""

import asyncio
import json

import pytest

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.discovery import build_discover_result
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
from nitrostack.transports.cors import build_cors_headers, cors_preflight_response_headers
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
            assert resp["result"]["protocolVersion"] == "2026-07-28"

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
