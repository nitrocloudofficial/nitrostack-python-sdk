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
            assert await pipeline.handle_post(body, {}) is None

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
            status, resp = await pipeline.handle_post(body, {})
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
                body, {"MCP-Protocol-Version": "2025-06-18"}
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
                        **modern_headers,
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
            assert "result" in init.json()
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
            assert call.headers.get("MCP-Protocol-Version") == MODERN_PROTOCOL_VERSION
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
