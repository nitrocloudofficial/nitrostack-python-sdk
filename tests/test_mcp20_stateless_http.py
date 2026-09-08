"""Tests for MCP 2.0 stateless HTTP transport."""

import asyncio
import json

import pytest

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.contracts import MCP_CACHE_HINT_KEY
from nitrostack.protocol.discovery import DISCOVER_RESULT_TYPE, SERVER_DISCOVER_METHOD, build_discover_result
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
    MAX_MCP_PARAM_VALUE_BYTES,
    build_mcp_echo_headers,
    build_mcp_response_headers,
    build_sse_stream_headers,
    extract_mcp_param_headers,
    first_oversized_mcp_param,
    handled_protocol_version,
    merge_mcp_param_headers,
    scope_without_session_headers,
    strip_legacy_session_headers,
    strip_legacy_session_headers_asgi,
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
        assert "Mcp-Method" not in headers
        echoed = build_mcp_response_headers(method="tools/call")
        assert echoed["Mcp-Method"] == "tools/call"

    def test_strip_asgi_session_headers_from_inner_scope(self):
        headers = [
            (b"content-type", b"application/json"),
            (b"mcp-session-id", b"forged"),
            (b"Mcp-Session-Id", b"also"),
        ]
        cleaned = strip_legacy_session_headers_asgi(headers)
        assert cleaned == [(b"content-type", b"application/json")]
        scope = scope_without_session_headers({"type": "http", "headers": headers})
        assert all(key.lower() != b"mcp-session-id" for key, _ in scope["headers"])
        assert scope["type"] == "http"


class TestHeaderCompatPreservesProtocolVersion:
    def test_inner_app_sees_original_protocol_version(self):
        from starlette.testclient import TestClient

        from nitrostack.transports.http import HeaderCompatMiddleware

        captured: dict[str, list] = {}

        async def inner(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            captured["headers"] = list(scope.get("headers") or [])
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

        wrapped = HeaderCompatMiddleware(inner, drop_session_headers=True)
        with TestClient(wrapped) as client:
            response = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "*/*",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Session-Id": "forged",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
        assert response.status_code == 200
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in captured["headers"]}
        assert headers["mcp-protocol-version"] == "2026-07-28"
        assert "mcp-session-id" not in headers
        assert "application/json" in headers["accept"]
        assert "text/event-stream" in headers["accept"]

    def test_unknown_protocol_version_is_not_dropped(self):
        from starlette.testclient import TestClient

        from nitrostack.transports.http import HeaderCompatMiddleware

        captured: dict[str, list] = {}

        async def inner(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            captured["headers"] = list(scope.get("headers") or [])
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

        wrapped = HeaderCompatMiddleware(inner)
        with TestClient(wrapped) as client:
            client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "1999-01-01",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in captured["headers"]}
        assert headers["mcp-protocol-version"] == "1999-01-01"


class TestMcpParamHeaderMirroring:
    def test_extract_is_case_insensitive(self):
        params = extract_mcp_param_headers(
            {"Mcp-Param-City": "Boston", "mcp-param-limit": "10", "Mcp-Name": "echo"}
        )
        assert params["City"] == "Boston"
        assert params["limit"] == "10"
        assert "Name" not in params

    def test_merge_fills_missing_and_keeps_body(self):
        merged = merge_mcp_param_headers(
            {"value": "body", "extra": ""},
            {"value": "header", "extra": "from-header", "unknown": "x"},
            allowed_fields={"value", "extra"},
        )
        assert merged["value"] == "body"
        assert merged["extra"] == "from-header"
        assert "unknown" not in merged

    def test_merge_does_not_override_name(self):
        merged = merge_mcp_param_headers(
            {"name": "echo", "value": ""},
            {"name": "other", "value": "ok"},
            allowed_fields={"name", "value"},
        )
        assert merged["name"] == "echo"
        assert merged["value"] == "ok"

    def test_oversized_param_is_detected(self):
        huge = "x" * (MAX_MCP_PARAM_VALUE_BYTES + 1)
        assert first_oversized_mcp_param({"Mcp-Param-City": huge}) == "City"
        assert first_oversized_mcp_param({"Mcp-Param-City": "Boston"}) is None

    def test_pipeline_name_check_uses_body_not_mirrored_params(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {}},
                }
            ).encode()
            result = await pipeline.handle_post(
                body,
                {
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "echo",
                    "Mcp-Param-Name": "other",
                },
            )
            assert result is None

        asyncio.run(_run())

    def test_pipeline_rejects_oversized_param(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body,
                {"Mcp-Param-City": "x" * (MAX_MCP_PARAM_VALUE_BYTES + 1)},
            )
            assert status == 400
            assert resp["error"]["code"] == -32600

        asyncio.run(_run())

    def test_http_header_fills_missing_tool_argument(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> dict:
                    return {
                        "value": input.value,
                        "mirrored": context.mcp_param_headers,
                    }

            @module(name="McpParamHttp", controllers=[EchoController])
            class McpParamModule:
                pass

            @mcp_app(module=McpParamModule, server=ServerConfig(name="mcp-param-http"))
            class McpParamApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(McpParamApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "echo",
                "MCP-Protocol-Version": "2025-06-18",
            }
            with TestClient(http_app) as client:
                filled = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Param-value": "from-header"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {}},
                    },
                )
                kept = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Param-value": "from-header"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "from-body"}},
                    },
                )
            assert filled.status_code == 200, filled.text
            filled_body = filled.json()["result"]
            filled_payload = filled_body.get("structuredContent") or json.loads(
                filled_body["content"][0]["text"]
            )
            assert filled_payload["value"] == "from-header"
            assert filled_payload["mirrored"]["value"] == "from-header"
            assert kept.status_code == 200, kept.text
            kept_body = kept.json()["result"]
            kept_payload = kept_body.get("structuredContent") or json.loads(
                kept_body["content"][0]["text"]
            )
            assert kept_payload["value"] == "from-body"
            assert kept_payload["mirrored"]["value"] == "from-header"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestCors:
    def test_cors_allow_methods(self):
        headers = build_cors_headers()
        assert "GET, POST, DELETE, OPTIONS" in headers["Access-Control-Allow-Methods"]
        assert "Mcp-Method" in headers["Access-Control-Allow-Headers"]
        expose = headers["Access-Control-Expose-Headers"]
        assert "MCP-Protocol-Version" in expose
        assert "Mcp-Method" in expose
        assert "Mcp-Name" in expose
        assert "Mcp-Session-Id" not in expose

    def test_preflight_headers(self):
        headers = cors_preflight_response_headers(
            {
                "Origin": "https://app.example.com",
                "Access-Control-Request-Headers": "Mcp-Name, Mcp-Method, MCP-Protocol-Version, Mcp-Param-City",
            }
        )
        assert "Access-Control-Allow-Origin" in headers
        allow = headers["Access-Control-Allow-Headers"]
        assert "Mcp-Name" in allow
        assert "Mcp-Method" in allow
        assert "MCP-Protocol-Version" in allow
        assert "Mcp-Param-City" in allow

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

    def test_forwards_server_discover_to_engine_handler(self):
        async def _run():
            payload = build_discover_result(
                server_name="srv",
                server_version="1.0.0",
                protocol_version=MODERN_PROTOCOL_VERSION,
            )

            def _discover(_request):
                return payload

            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION),
                discover_handler=_discover,
            )
            body = json.dumps(
                {"jsonrpc": "2.0", "id": "req-001", "method": SERVER_DISCOVER_METHOD, "params": {}}
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            result = resp["result"]
            assert result == payload
            assert result["protocolVersion"] == "2026-07-28"
            assert result["resultType"] == DISCOVER_RESULT_TYPE
            assert MODERN_PROTOCOL_VERSION in result["supportedVersions"]

        asyncio.run(_run())

    def test_server_discover_without_engine_handler_is_not_answered(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps(
                {"jsonrpc": "2.0", "id": "req-001", "method": SERVER_DISCOVER_METHOD, "params": {}}
            ).encode()
            assert await pipeline.handle_post(body, {}) is None

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
            assert await pipeline.handle_post(
                body, {"Mcp-Name": "demo", "Mcp-Method": "tools/call"}
            ) is None

        asyncio.run(_run())

    def test_tools_call_requires_mcp_name(self):
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
            status, resp = await pipeline.handle_post(body, {"Mcp-Method": "tools/call"})
            assert status == 400
            assert resp["error"]["code"] == -32020
            assert "Mcp-Name" in resp["error"]["message"]

        asyncio.run(_run())

    def test_tools_call_rejects_mcp_name_mismatch(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "bar"},
                }
            ).encode()
            status, resp = await pipeline.handle_post(
                body, {"Mcp-Name": "foo", "Mcp-Method": "tools/call"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32020
            assert "foo" in resp["error"]["message"]
            assert "bar" in resp["error"]["message"]

        asyncio.run(_run())

    def test_ping_does_not_require_mcp_name(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            assert resp["result"] == {}

        asyncio.run(_run())

    def test_modern_reject_initialize(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="reject")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2026-07-28",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                }
            ).encode()
            status, resp = await pipeline.handle_post(body, {"Mcp-Method": "initialize"})
            assert status == 200
            assert resp["error"]["code"] == -32601

        asyncio.run(_run())

    def test_modern_reject_legacy_protocol_version(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="reject")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "ping"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32022

        asyncio.run(_run())

    def test_modern_reject_incoming_session_id(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="reject")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {LEGACY_SESSION_HEADER: "session-1"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32600

        asyncio.run(_run())

    def test_auto_reject_incoming_session_id(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "echo"},
                }
            ).encode()
            status, resp = await pipeline.handle_post(
                body, {LEGACY_SESSION_HEADER: "session-1"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32600
            assert resp["id"] == 4

        asyncio.run(_run())

    def test_legacy_sessionful_pipeline_keeps_session_id(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="sessionful")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {LEGACY_SESSION_HEADER: "session-1"}
            )
            assert status == 200
            assert resp["result"] == {}

        asyncio.run(_run())

    def test_auto_accepts_initialize(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                }
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            result = resp["result"]
            assert result["protocolVersion"] == "2025-06-18"
            assert result["serverInfo"]["name"] == "srv"
            assert "capabilities" in result

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
                bare_health = client.options("/health")
                oauth_opt = client.options("/oauth/v2/register")
                health_preflight = client.options(
                    "/mcp/health",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                oauth_preflight = client.options(
                    "/oauth/v2/register",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                call = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
            assert mcp_opt.status_code == 204
            assert "Access-Control-Allow-Origin" in mcp_opt.headers
            assert health_opt.status_code != 204
            assert bare_health.status_code != 204
            assert oauth_opt.status_code != 204
            assert health_preflight.status_code != 204
            assert oauth_preflight.status_code != 204
            assert call.status_code == 200
            assert call.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()

    def test_options_mcp_is_not_sidecar_204_when_cors_off(self):
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

            @module(name="OptionsCorsOffHttp", controllers=[EchoController])
            class OptionsCorsOffModule:
                pass

            @mcp_app(module=OptionsCorsOffModule, server=ServerConfig(name="options-cors-off"))
            class OptionsCorsOffApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(OptionsCorsOffApp))
            http_app = app.get_combined_app(stateless=True, json_response=True, enable_cors=False)
            with TestClient(http_app) as client:
                mcp_opt = client.options("/mcp")
            assert mcp_opt.status_code != 204
        finally:
            DIContainer.reset()

    def test_options_allow_headers_include_2026_mcp_names(self, monkeypatch):
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer
        from pydantic import BaseModel, Field

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        monkeypatch.setenv("MCP_CORS_ALLOWED_ORIGINS", "https://app.example.com")
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="CorsAllowHeadersHttp", controllers=[EchoController])
            class CorsAllowHeadersModule:
                pass

            @mcp_app(module=CorsAllowHeadersModule, server=ServerConfig(name="cors-allow-headers"))
            class CorsAllowHeadersApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(CorsAllowHeadersApp))
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                allowed = client.options(
                    "/mcp",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": (
                            "Mcp-Name, Mcp-Method, MCP-Protocol-Version, Mcp-Param-City"
                        ),
                    },
                )
                unknown = client.options(
                    "/mcp",
                    headers={
                        "Origin": "https://evil.example",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "Mcp-Name",
                    },
                )
            assert allowed.status_code == 204
            allow = allowed.headers.get("access-control-allow-headers", "")
            assert "Mcp-Name" in allow
            assert "Mcp-Method" in allow
            assert "MCP-Protocol-Version" in allow
            assert "Mcp-Param-City" in allow
            assert allowed.headers.get("access-control-allow-origin") == "https://app.example.com"
            assert unknown.headers.get("access-control-allow-origin") != "https://evil.example"
        finally:
            DIContainer.reset()
            monkeypatch.delenv("MCP_CORS_ALLOWED_ORIGINS", raising=False)
            monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)


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


class TestNitroMcpProtocolVersionEnv:
    def test_modern_era_enables_stateless_without_mcp_stateless(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "2026-07-28")
        monkeypatch.delenv("MCP_STATELESS", raising=False)

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="EraHttp", controllers=[EchoController])
            class EraModule:
                pass

            @mcp_app(module=EraModule, server=ServerConfig(name="era-http"))
            class EraApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(EraApp))
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                mcp_opt = client.options("/mcp")
                call = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Mcp-Method": "ping",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "ping",
                    },
                )
            assert mcp_opt.status_code == 204
            assert call.status_code == 200
            assert call.json()["result"] == {}
            assert app.protocol_era == "modern"
            inner = getattr(http_app, "app", http_app)
            assert inner.state.protocol_era == "modern"
            assert inner.state.wire_mode == "reject"
            assert inner.state.stateless is True
            assert inner.state.http_engine == "sessionless"
            assert app.mcp_server.http_engine == "sessionless"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_auto_era_keeps_era_and_does_not_force_stateless(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="AutoEraHttp", controllers=[EchoController])
            class AutoEraModule:
                pass

            @mcp_app(module=AutoEraModule, server=ServerConfig(name="auto-era-http"))
            class AutoEraApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(AutoEraApp))
            http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert app.protocol_era == "auto"
            assert state.protocol_era == "auto"
            assert state.wire_mode == "stateless"
            assert state.stateless is True
            assert state.http_engine == "sessionless"
            assert state.streamable_http_manager_count == 1
            assert app.mcp_server.http_engine == "sessionless"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_legacy_era_uses_sessionful_engine(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "legacy")
        monkeypatch.delenv("MCP_STATELESS", raising=False)

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="LegacyEngineHttp", controllers=[EchoController])
            class LegacyEngineModule:
                pass

            @mcp_app(module=LegacyEngineModule, server=ServerConfig(name="legacy-engine-http"))
            class LegacyEngineApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(LegacyEngineApp))
            http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert app.protocol_era == "legacy"
            assert state.http_engine == "sessionful"
            assert state.stateless is False
            assert state.session_manager.stateless is False
            assert app.mcp_server.http_engine == "sessionful"
            assert "echo" in app._tools
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


def _http_app_state(asgi):
    current = asgi
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        state = getattr(current, "state", None)
        if state is not None and getattr(state, "protocol_era", None) is not None:
            return state
        current = getattr(current, "app", None)
    raise AssertionError("HTTP app is missing protocol era state")


class TestAutoEraOneMcpDualClients:
    def test_auto_serves_initialize_and_discover_on_one_mcp(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer
        from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="DualClientHttp", controllers=[EchoController])
            class DualClientModule:
                pass

            @mcp_app(module=DualClientModule, server=ServerConfig(name="dual-client-http"))
            class DualClientApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(DualClientApp))
            http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert state.protocol_era == "auto"
            assert state.streamable_http_manager_count == 1
            assert state.session_manager.stateless is True

            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            modern_headers = {
                **json_headers,
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
            }

            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "legacy-client", "version": "1.0"},
                        },
                    },
                )
                discover = client.post(
                    "/mcp",
                    headers={**modern_headers, "Mcp-Method": "server/discover"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "server/discover",
                        "params": {},
                    },
                )
                call = client.post(
                    "/mcp",
                    headers={
                        **json_headers,
                        "MCP-Protocol-Version": "2025-06-18",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )

            assert init.status_code == 200, init.text
            init_body = init.json()["result"]
            expected_init = app.handle_sessionless_initialize("2025-06-18")
            assert init_body == expected_init
            assert app.mcp_server.handle_sessionless_initialize("2025-06-18") == expected_init
            assert init_body["protocolVersion"] == "2025-06-18"
            assert "serverInfo" in init_body
            assert "capabilities" in init_body
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances

            assert discover.status_code == 200, discover.text
            discover_body = discover.json()["result"]
            expected_discover = app.handle_server_discover()
            assert discover_body == expected_discover
            assert app.mcp_server.handle_server_discover() == expected_discover
            assert discover_body["protocolVersion"] == MODERN_PROTOCOL_VERSION
            assert discover.headers.get("MCP-Protocol-Version") == MODERN_PROTOCOL_VERSION

            assert call.status_code == 200, call.text
            assert call.json()["result"]["content"][0]["text"] == "ok"
            assert call.headers.get("MCP-Protocol-Version") == "2025-06-18"
            assert call.headers.get("Mcp-Method") == "tools/call"
            call_headers = {key.lower(): value for key, value in call.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in call_headers
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestModernEraRejectsLegacyWire:
    def test_modern_initialize_is_jsonrpc_error_without_session(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "modern")
        monkeypatch.delenv("MCP_STATELESS", raising=False)

        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="ModernRejectHttp", controllers=[EchoController])
            class ModernRejectModule:
                pass

            @mcp_app(module=ModernRejectModule, server=ServerConfig(name="modern-reject-http"))
            class ModernRejectApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(ModernRejectApp))
            http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert state.wire_mode == "reject"

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "legacy-client", "version": "1.0"},
                        },
                    },
                )
                initialized = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                with_session = client.post(
                    "/mcp",
                    headers={**headers, LEGACY_SESSION_HEADER: "forged"},
                    json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
                )

            assert init.status_code in (200, 400)
            assert "error" in init.json()
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances

            assert initialized.status_code in (200, 400)
            assert "error" in initialized.json()

            assert with_session.status_code == 400
            assert with_session.json()["error"]["code"] == -32600
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestHealthAdvertisesEra:
    def _health_body(self, monkeypatch, era_value: str) -> dict:
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", era_value)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="HealthEraHttp", controllers=[EchoController])
            class HealthEraModule:
                pass

            @mcp_app(module=HealthEraModule, server=ServerConfig(name="health-era-http"))
            class HealthEraApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(HealthEraApp))
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                response = client.get("/mcp/health")
            assert response.status_code == 200
            return response.json()
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_modern_health_reports_modern_era(self, monkeypatch):
        body = self._health_body(monkeypatch, "modern")
        assert body["status"] == "ok"
        assert body["transport"] == "streamable-http"
        assert body["protocolEra"] == "modern"
        assert body["protocolVersion"] == "2026-07-28"
        assert body["statelessCapable"] is True
        assert "stateless" in body
        assert "uptimeSeconds" in body

    def test_auto_health_reports_auto_not_modern(self, monkeypatch):
        body = self._health_body(monkeypatch, "auto")
        assert body["protocolEra"] == "auto"
        assert body["protocolEra"] != "modern"
        assert body["statelessCapable"] is True

    def test_legacy_health_reports_legacy_era(self, monkeypatch):
        body = self._health_body(monkeypatch, "legacy")
        assert body["protocolEra"] == "legacy"
        assert body["protocolVersion"] == "2025-06-18"
        assert body["statelessCapable"] is False


class TestTrustedProxyPublicUrl:
    def _http_app(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.delenv("MCP_STATELESS", raising=False)
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        DIContainer.reset()

        @injectable()
        class EchoController:
            @tool(name="echo", description="echo", input_schema=EchoInput)
            async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="ProxyHttp", controllers=[EchoController])
        class ProxyModule:
            pass

        @mcp_app(module=ProxyModule, server=ServerConfig(name="proxy-http"))
        class ProxyApp:
            pass

        app = asyncio.run(McpApplicationFactory.create(ProxyApp))
        return app.get_combined_app(json_response=True)

    def test_health_ignores_untrusted_forwarded_host(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            http_app = self._http_app(monkeypatch)
            with TestClient(http_app, client=("8.8.8.8", 4321)) as client:
                response = client.get(
                    "/mcp/health",
                    headers={
                        "Host": "internal:3000",
                        "X-Forwarded-Host": "mcp.example.com",
                        "X-Forwarded-Proto": "https",
                    },
                )
                docs = client.get(
                    "/",
                    headers={
                        "Host": "internal:3000",
                        "X-Forwarded-Host": "mcp.example.com",
                        "X-Forwarded-Proto": "https",
                    },
                )
                oauth = client.get(
                    "/.well-known/oauth-authorization-server",
                    headers={
                        "Host": "internal:3000",
                        "X-Forwarded-Host": "mcp.example.com",
                        "X-Forwarded-Proto": "https",
                    },
                )
            assert response.status_code == 200
            assert "mcp.example.com" not in response.json()["publicUrl"]
            assert "mcp.example.com" not in docs.text
            assert "mcp.example.com" not in oauth.json()["error_description"]
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_health_honors_trusted_forwarded_host(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        try:
            http_app = self._http_app(monkeypatch)
            with TestClient(http_app, client=("10.0.0.5", 4321)) as client:
                headers = {
                    "Host": "internal:3000",
                    "X-Forwarded-Host": "mcp.example.com",
                    "X-Forwarded-Proto": "https",
                }
                response = client.get("/mcp/health", headers=headers)
                docs = client.get("/", headers=headers)
                oauth = client.get("/.well-known/oauth-authorization-server", headers=headers)
                version = client.get("/json/version", headers=headers)
            assert response.status_code == 200
            assert response.json()["publicUrl"] == "https://mcp.example.com/mcp"
            assert "https://mcp.example.com/mcp" in docs.text
            assert "https://mcp.example.com/mcp" in oauth.json()["error_description"]
            assert version.json()["publicUrl"] == "https://mcp.example.com/mcp"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)
            os.environ.pop("TRUSTED_PROXIES", None)


class TestEnvelopeOnHandlerContext:
    def test_tool_reads_trace_and_protocol_version_not_spoofed_user(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EmptyInput(BaseModel):
            pass

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EnvelopeController:
                @tool(name="envelope", description="read envelope", input_schema=EmptyInput)
                async def envelope(self, input: EmptyInput, context: ExecutionContext) -> dict:
                    return {
                        "protocolVersion": context.protocol_version,
                        "trace": context.rpc_meta.trace if context.rpc_meta else None,
                        "user": context.user,
                    }

            @module(name="EnvelopeHttp", controllers=[EnvelopeController])
            class EnvelopeModule:
                pass

            @mcp_app(module=EnvelopeModule, server=ServerConfig(name="envelope-http"))
            class EnvelopeApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(EnvelopeApp))
            http_app = app.get_combined_app(json_response=True)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "envelope",
            }
            with TestClient(http_app) as client:
                response = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "envelope",
                            "arguments": {},
                            "_meta": {
                                "trace": {"id": "http-span"},
                                "userId": "spoofed",
                            },
                        },
                    },
                )

            assert response.status_code == 200, response.text
            result = response.json()["result"]
            body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
            assert body["protocolVersion"] == "2025-06-18"
            assert body["trace"] == {"id": "http-span"}
            assert body["user"] is None
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestAuthFromEnvelopeAndHeaders:
    def test_tool_user_comes_from_header_jwt_not_unsigned_meta(self, monkeypatch):
        import os

        from pydantic import BaseModel
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.auth.jwt import JWTService
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EmptyInput(BaseModel):
            pass

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        jwt = JWTService()
        DIContainer.get_instance().register_value(JWTService, jwt)
        token = jwt.create_token({"sub": "alice", "tenant_id": "acme"})
        try:
            @injectable()
            class AuthController:
                @tool(name="whoami", description="identity", input_schema=EmptyInput)
                async def whoami(self, input: EmptyInput, context: ExecutionContext) -> dict:
                    return {"user": context.user}

            @module(name="AuthHttp", controllers=[AuthController])
            class AuthModule:
                pass

            @mcp_app(module=AuthModule, server=ServerConfig(name="auth-http"))
            class AuthApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(AuthApp))
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                response = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-06-18",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "whoami",
                        "Authorization": f"Bearer {token}",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "whoami",
                            "arguments": {},
                            "_meta": {
                                "userId": "eve",
                                "tenantId": "evil",
                                "io.modelcontextprotocol/auth": {"userId": "mallory"},
                            },
                        },
                    },
                )

            assert response.status_code == 200, response.text
            result = response.json()["result"]
            body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
            assert body["user"] == "alice"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestIncomingSessionIdRejection:
    def _echo_app(self, monkeypatch, era_value: str):
        import os

        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", era_value)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()

        @injectable()
        class EchoController:
            @tool(name="echo", description="echo", input_schema=EchoInput)
            async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="SessionRejectHttp", controllers=[EchoController])
        class SessionRejectModule:
            pass

        @mcp_app(module=SessionRejectModule, server=ServerConfig(name="session-reject-http"))
        class SessionRejectApp:
            pass

        return asyncio.run(McpApplicationFactory.create(SessionRejectApp))

    def test_modern_post_with_session_id_is_rejected(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "modern")
            http_app = app.get_combined_app(json_response=True)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                LEGACY_SESSION_HEADER: "forged",
            }
            with TestClient(http_app) as client:
                rejected = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                )
                allowed = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Mcp-Method": "ping",
                    },
                    json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
                )
            assert rejected.status_code == 400
            assert rejected.json()["error"]["code"] == -32600
            assert allowed.status_code == 200
            assert allowed.json()["result"] == {}
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_auto_post_with_session_id_is_rejected(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "auto")
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                rejected = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                        LEGACY_SESSION_HEADER: "forged",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
                allowed = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
            assert rejected.status_code == 400
            assert rejected.json()["error"]["code"] == -32600
            assert allowed.status_code == 200
            assert allowed.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_legacy_post_accepts_session_id_after_initialize(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "legacy")
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "legacy-client", "version": "1.0"},
                        },
                    },
                )
                session_id = init.headers.get("mcp-session-id") or init.headers.get(
                    "Mcp-Session-Id"
                )
                assert init.status_code == 200, init.text
                assert session_id
                client.post(
                    "/mcp",
                    headers={**json_headers, LEGACY_SESSION_HEADER: session_id},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                call = client.post(
                    "/mcp",
                    headers={
                        **json_headers,
                        LEGACY_SESSION_HEADER: session_id,
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
            assert call.status_code == 200, call.text
            assert call.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestSessionIdNotForwarded:
    def test_forwarded_scope_omits_session_header(self):
        from starlette.testclient import TestClient

        from nitrostack.transports.middleware import wrap_stateless_transport

        captured: dict[str, list] = {}

        async def inner(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            captured["headers"] = list(scope.get("headers") or [])
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"jsonrpc":"2.0","id":1,"result":{}}',
                }
            )

        wrapped = wrap_stateless_transport(
            inner,
            server_name="srv",
            server_version="1.0.0",
            protocol_version=MODERN_PROTOCOL_VERSION,
            wire_mode="sessionful",
        )
        with TestClient(wrapped) as client:
            response = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    LEGACY_SESSION_HEADER: "forged",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "echo",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "echo"},
                },
            )
        assert response.status_code == 200
        assert "headers" in captured
        assert all(key.lower() != b"mcp-session-id" for key, _ in captured["headers"])

    def test_forged_session_id_cannot_associate_two_modern_calls(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "modern")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="NoSharedSessionHttp", controllers=[EchoController])
            class NoSharedSessionModule:
                pass

            @mcp_app(module=NoSharedSessionModule, server=ServerConfig(name="no-shared-session"))
            class NoSharedSessionApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(NoSharedSessionApp))
            http_app = app.get_combined_app(json_response=True)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "echo",
                LEGACY_SESSION_HEADER: "forged-shared",
            }
            body = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"value": "ok"}},
            }
            with TestClient(http_app) as client:
                first = client.post("/mcp", headers=headers, json={**body, "id": 1})
                second = client.post("/mcp", headers=headers, json={**body, "id": 2})
            assert first.status_code == 400
            assert second.status_code == 400
            assert first.json()["error"]["code"] == -32600
            assert second.json()["error"]["code"] == -32600
            state = _http_app_state(http_app)
            assert not getattr(state.session_manager, "_server_instances", True)
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_modern_get_mcp_rejects_session_id(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "modern")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="ModernGetSessionHttp", controllers=[EchoController])
            class ModernGetSessionModule:
                pass

            @mcp_app(module=ModernGetSessionModule, server=ServerConfig(name="modern-get-session"))
            class ModernGetSessionApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(ModernGetSessionApp))
            http_app = app.get_combined_app(json_response=True)
            with TestClient(http_app) as client:
                response = client.get(
                    "/mcp",
                    headers={
                        "Accept": "text/event-stream",
                        LEGACY_SESSION_HEADER: "forged",
                    },
                )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == -32600
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestRequiredMcpName:
    def test_http_tools_call_missing_and_mismatch_and_match(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="RequiredNameHttp", controllers=[EchoController])
            class RequiredNameModule:
                pass

            @mcp_app(module=RequiredNameModule, server=ServerConfig(name="required-name-http"))
            class RequiredNameApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(RequiredNameApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/call",
            }
            call_body = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"value": "ok"}},
            }
            with TestClient(http_app) as client:
                missing = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={**call_body, "id": 1},
                )
                mismatch = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Name": "foo"},
                    json={**call_body, "id": 2},
                )
                matched = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Name": "echo"},
                    json={**call_body, "id": 3},
                )
            assert missing.status_code == 400
            assert missing.json()["error"]["code"] == -32020
            assert mismatch.status_code == 400
            assert mismatch.json()["error"]["code"] == -32020
            assert matched.status_code == 200, matched.text
            assert matched.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestRequiredMcpMethod:
    def test_pipeline_tools_call_requires_mcp_method(self):
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
            missing = await pipeline.handle_post(body, {"Mcp-Name": "demo"})
            mismatch = await pipeline.handle_post(
                body, {"Mcp-Name": "demo", "Mcp-Method": "tools/list"}
            )
            matched = await pipeline.handle_post(
                body, {"Mcp-Name": "demo", "Mcp-Method": "tools/call"}
            )
            assert missing is not None
            assert missing[0] == 400
            assert missing[1]["error"]["code"] == -32020
            assert mismatch is not None
            assert mismatch[0] == 400
            assert mismatch[1]["error"]["code"] == -32020
            assert matched is None

        asyncio.run(_run())

    def test_auto_initialize_does_not_require_mcp_method(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                }
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            assert resp["result"]["protocolVersion"] == "2025-06-18"

        asyncio.run(_run())

    def test_modern_ping_requires_mcp_method(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="reject")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            missing = await pipeline.handle_post(body, {})
            matched = await pipeline.handle_post(body, {"Mcp-Method": "ping"})
            assert missing is not None
            assert missing[0] == 400
            assert missing[1]["error"]["code"] == -32020
            assert matched is not None
            assert matched[0] == 200
            assert matched[1]["result"] == {}

        asyncio.run(_run())

    def test_http_tools_call_missing_and_mismatch_and_match(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="RequiredMethodHttp", controllers=[EchoController])
            class RequiredMethodModule:
                pass

            @mcp_app(module=RequiredMethodModule, server=ServerConfig(name="required-method-http"))
            class RequiredMethodApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(RequiredMethodApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Name": "echo",
            }
            call_body = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"value": "ok"}},
            }
            with TestClient(http_app) as client:
                missing = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={**call_body, "id": 1},
                )
                mismatch = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Method": "tools/list"},
                    json={**call_body, "id": 2},
                )
                matched = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Method": "tools/call"},
                    json={**call_body, "id": 3},
                )
            assert missing.status_code == 400
            assert missing.json()["error"]["code"] == -32020
            assert mismatch.status_code == 400
            assert mismatch.json()["error"]["code"] == -32020
            assert matched.status_code == 200, matched.text
            assert matched.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestProtocolVersionCrossCheck:
    def test_http_mismatch_is_rejected_and_header_only_succeeds(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="VersionCrossCheckHttp", controllers=[EchoController])
            class VersionCrossCheckModule:
                pass

            @mcp_app(module=VersionCrossCheckModule, server=ServerConfig(name="version-cross-check"))
            class VersionCrossCheckApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(VersionCrossCheckApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "echo",
            }
            with TestClient(http_app) as client:
                mismatch = client.post(
                    "/mcp",
                    headers={**json_headers, "MCP-Protocol-Version": "2026-07-28"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "echo",
                            "arguments": {"value": "ok"},
                            "_meta": {"mcp": {"protocolVersion": "2025-06-18"}},
                        },
                    },
                )
                header_only = client.post(
                    "/mcp",
                    headers={**json_headers, "MCP-Protocol-Version": "2025-06-18"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
            assert mismatch.status_code == 400
            assert mismatch.json()["error"]["code"] == -32020
            assert header_only.status_code == 200, header_only.text
            assert header_only.json()["result"]["content"][0]["text"] == "ok"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestUnsupportedProtocolVersionHttp:
    def test_http_unknown_version_is_rejected_and_supported_proceeds(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="UnsupportedVersionHttp", controllers=[EchoController])
            class UnsupportedVersionModule:
                pass

            @mcp_app(module=UnsupportedVersionModule, server=ServerConfig(name="unsupported-version"))
            class UnsupportedVersionApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(UnsupportedVersionApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            with TestClient(http_app) as client:
                unknown = client.post(
                    "/mcp",
                    headers={**json_headers, "MCP-Protocol-Version": "1999-01-01"},
                    json=ping,
                )
                supported = client.post(
                    "/mcp",
                    headers={**json_headers, "MCP-Protocol-Version": "2026-07-28"},
                    json={**ping, "id": 2},
                )
                legacy = client.post(
                    "/mcp",
                    headers={**json_headers, "MCP-Protocol-Version": "2025-06-18"},
                    json={**ping, "id": 3},
                )
            assert unknown.status_code == 400
            assert unknown.json()["error"]["code"] == -32022
            assert unknown.json()["error"]["message"] == "Unsupported protocol version"
            assert supported.status_code == 200, supported.text
            assert supported.json()["result"] == {}
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()["result"] == {}
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestResponseEchoHeaders:
    def test_echo_uses_supported_request_version(self):
        headers = build_mcp_echo_headers(
            {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "tools/call"},
            protocol_version=MODERN_PROTOCOL_VERSION,
            supported_versions={"2026-07-28", "2025-06-18"},
        )
        assert headers["MCP-Protocol-Version"] == "2025-06-18"
        assert headers["Mcp-Method"] == "tools/call"
        assert handled_protocol_version(
            {"MCP-Protocol-Version": "1999-01-01"},
            fallback=MODERN_PROTOCOL_VERSION,
            supported={"2026-07-28", "2025-06-18"},
        ) == MODERN_PROTOCOL_VERSION

    def test_http_success_and_error_echo_headers(self, monkeypatch):
        import os

        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="EchoHeadersHttp", controllers=[EchoController])
            class EchoHeadersModule:
                pass

            @mcp_app(module=EchoHeadersModule, server=ServerConfig(name="echo-headers"))
            class EchoHeadersApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(EchoHeadersApp))
            http_app = app.get_combined_app(json_response=True)
            json_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "echo",
            }
            with TestClient(http_app) as client:
                success = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
                mismatch = client.post(
                    "/mcp",
                    headers={**json_headers, "Mcp-Name": "other"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
                unsupported = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "1999-01-01",
                    },
                    json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
                )
            assert success.status_code == 200, success.text
            assert success.headers.get("MCP-Protocol-Version") == "2025-06-18"
            assert success.headers.get("Mcp-Method") == "tools/call"
            assert mismatch.status_code == 400
            assert mismatch.json()["error"]["code"] == -32020
            assert mismatch.headers.get("MCP-Protocol-Version") == "2025-06-18"
            assert mismatch.headers.get("Mcp-Method") == "tools/call"
            assert unsupported.status_code == 400
            assert unsupported.json()["error"]["code"] == -32022
            assert unsupported.headers.get("MCP-Protocol-Version") == MODERN_PROTOCOL_VERSION
            assert unsupported.headers.get("Mcp-Method") == "ping"
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


class TestCorsExposeHeaders:
    def test_http_mcp_response_exposes_echo_headers_not_session_id(self, monkeypatch):
        from pydantic import BaseModel, Field
        from starlette.testclient import TestClient

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.di import DIContainer

        class EchoInput(BaseModel):
            value: str = Field(default="")

        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            @injectable()
            class EchoController:
                @tool(name="echo", description="echo", input_schema=EchoInput)
                async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                    return input.value

            @module(name="CorsExposeHttp", controllers=[EchoController])
            class CorsExposeModule:
                pass

            @mcp_app(module=CorsExposeModule, server=ServerConfig(name="cors-expose"))
            class CorsExposeApp:
                pass

            app = asyncio.run(McpApplicationFactory.create(CorsExposeApp))
            http_app = app.get_combined_app(json_response=True)
            origin = "https://app.example.com"
            with TestClient(http_app) as client:
                response = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Origin": origin,
                        "MCP-Protocol-Version": "2025-06-18",
                        "Mcp-Method": "tools/call",
                        "Mcp-Name": "echo",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"value": "ok"}},
                    },
                )
                preflight = client.options(
                    "/mcp",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "POST",
                    },
                )
            assert response.status_code == 200, response.text
            expose = response.headers.get("access-control-expose-headers", "")
            assert "MCP-Protocol-Version" in expose
            assert "Mcp-Method" in expose
            assert "Mcp-Session-Id" not in expose
            assert preflight.status_code == 204
            preflight_expose = preflight.headers.get("access-control-expose-headers", "")
            assert "MCP-Protocol-Version" in preflight_expose
            assert "Mcp-Method" in preflight_expose
            assert "Mcp-Session-Id" not in preflight_expose
        finally:
            DIContainer.reset()
            monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
