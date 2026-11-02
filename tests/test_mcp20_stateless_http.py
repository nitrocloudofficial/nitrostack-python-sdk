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
    parse_jsonrpc_request,
    validate_header_body_method,
)
from nitrostack.protocol.method_contract import (
    MODERN_METHOD_CONTRACTS,
    mcp_name_field,
    mcp_name_is_required,
)
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.cors import build_cors_headers, cors_preflight_response_headers, resolve_allowed_origin
from nitrostack.transports.headers import (
    MAX_MCP_PARAM_VALUE_BYTES,
    build_mcp_echo_headers,
    build_mcp_response_headers,
    build_sse_stream_headers,
    extract_mcp_param_headers,
    first_oversized_mcp_param,
    handled_protocol_version,
    merge_mcp_param_headers,
    scope_with_header_snapshot,
    scope_without_session_headers,
    snapshot_validated_asgi_headers,
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

    def test_replay_snapshot_keeps_contract_headers_and_drops_session_id(self):
        headers = [
            (b"content-type", b"application/json"),
            (b"mcp-method", b"tools/call"),
            (b"mcp-name", b"echo"),
            (b"mcp-protocol-version", b"2026-07-28"),
            (b"mcp-session-id", b"forged"),
        ]
        snapshot = snapshot_validated_asgi_headers(headers)
        assert all(key.lower() != b"mcp-session-id" for key, _ in snapshot)
        by_name = {key.lower(): value for key, value in snapshot}
        assert by_name[b"mcp-method"] == b"tools/call"
        assert by_name[b"mcp-name"] == b"echo"
        assert by_name[b"mcp-protocol-version"] == b"2026-07-28"
        live = {"type": "http", "headers": list(headers)}
        live["headers"].append((b"mcp-session-id", b"later"))
        replayed = scope_with_header_snapshot(live, snapshot)
        assert all(key.lower() != b"mcp-session-id" for key, _ in replayed["headers"])
        replayed_by_name = {key.lower(): value for key, value in replayed["headers"]}
        assert replayed_by_name[b"mcp-method"] == b"tools/call"
        assert replayed_by_name[b"mcp-name"] == b"echo"


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
            assert state.sessionful is True
            assert state.stateless is False
            assert state.session_manager.stateless is False
            assert app.mcp_server.http_engine == "sessionful"
            assert app.mcp_server.sessionful is True
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


class TestEraSessionfulCoexistence:
    def _echo_app(self, monkeypatch, era_value: str):
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

        @module(name=f"EraCoexist{era_value.title()}", controllers=[EchoController])
        class EraModule:
            pass

        @mcp_app(module=EraModule, server=ServerConfig(name=f"era-coexist-{era_value}"))
        class EraApp:
            pass

        return asyncio.run(McpApplicationFactory.create(EraApp))

    def test_auto_stateless_false_does_not_start_sessionful_manager(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "auto")
            http_app = app.get_combined_app(json_response=True, stateless=False)
            state = _http_app_state(http_app)
            assert state.http_engine == "sessionless"
            assert state.sessionful is False
            assert state.session_manager.stateless is True
            assert state.streamable_http_manager_count == 1
            assert app.mcp_server.sessionful is False
            assert "echo" in app._tools

            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
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
            assert init.status_code == 200, init.text
            assert "result" in init.json()
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_modern_stateless_false_still_rejects_initialize(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "modern")
            http_app = app.get_combined_app(json_response=True, stateless=False)
            state = _http_app_state(http_app)
            assert state.http_engine == "sessionless"
            assert state.sessionful is False
            assert "echo" in app._tools

            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
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
            assert init.status_code == 200
            assert init.json()["error"]["code"] == -32601
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)

    def test_legacy_initialize_creates_session_without_modern_headers(self, monkeypatch):
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        try:
            app = self._echo_app(monkeypatch, "legacy")
            http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert state.http_engine == "sessionful"
            assert state.sessionful is True
            assert state.streamable_http_manager_count == 1
            assert "echo" in app._tools

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
                    headers={**json_headers, LEGACY_SESSION_HEADER: session_id},
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


class TestUnsetEnvDefault:
    def _echo_app(self):
        from pydantic import BaseModel, Field

        from nitrostack import ExecutionContext, injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app

        class EchoInput(BaseModel):
            value: str = Field(default="")

        @injectable()
        class EchoController:
            @tool(name="echo", description="echo", input_schema=EchoInput)
            async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="UnsetEnvDefault", controllers=[EchoController])
        class UnsetEnvModule:
            pass

        @mcp_app(module=UnsetEnvModule, server=ServerConfig(name="unset-env-default"))
        class UnsetEnvApp:
            pass

        return asyncio.run(McpApplicationFactory.create(UnsetEnvApp))

    def test_unset_env_http_is_auto_sessionless(self, monkeypatch, caplog):
        import logging
        import os

        from starlette.testclient import TestClient

        from nitrostack.core.di import DIContainer

        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        DIContainer.reset()
        try:
            with caplog.at_level(logging.INFO, logger="nitrostack.core.app"):
                app = self._echo_app()
                http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert app.protocol_era == "auto"
            assert app.protocol_era_source == "default"
            assert state.protocol_era == "auto"
            assert state.http_engine == "sessionless"
            assert "protocol era=auto (source=default)" in caplog.text

            with TestClient(http_app) as client:
                init = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
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
            assert init.status_code == 200, init.text
            assert "result" in init.json()
            assert init.json()["result"]["protocolVersion"] == "2025-06-18"
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)
            os.environ.pop("MCP_STATELESS", None)

    def test_mcp_stateless_false_still_forces_legacy(self, monkeypatch, caplog):
        import logging
        import os

        from nitrostack.core.di import DIContainer

        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.setenv("MCP_STATELESS", "false")
        DIContainer.reset()
        try:
            with caplog.at_level(logging.INFO, logger="nitrostack.core.app"):
                app = self._echo_app()
                http_app = app.get_combined_app(json_response=True)
            state = _http_app_state(http_app)
            assert app.protocol_era == "legacy"
            assert app.protocol_era_source == "mcp_stateless"
            assert state.protocol_era == "legacy"
            assert state.http_engine == "sessionful"
            assert "protocol era=legacy (source=mcp_stateless)" in caplog.text
        finally:
            DIContainer.reset()
            os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)
            os.environ.pop("MCP_STATELESS", None)


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
                initialized = client.post(
                    "/mcp",
                    headers=json_headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                discover = client.post(
                    "/mcp",
                    headers={**modern_headers, "Mcp-Method": "server/discover"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "server/discover",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
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
            assert init_body["protocolVersion"] == "2025-06-18"
            assert "serverInfo" in init_body
            assert "capabilities" in init_body
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances

            assert initialized.status_code == 202, initialized.text
            if initialized.content:
                assert initialized.json() == {}
            initialized_headers = {
                key.lower(): value for key, value in initialized.headers.items()
            }
            assert LEGACY_SESSION_HEADER.lower() not in initialized_headers
            assert not state.session_manager._server_instances

            assert discover.status_code == 200, discover.text
            discover_body = discover.json()["result"]
            assert "capabilities" in discover_body
            assert discover_body["resultType"] == "complete"

            assert call.status_code == 200, call.text
            assert call.json()["result"]["content"][0]["text"] == "ok"
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
            initialize_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-client", "version": "1.0"},
                },
            }
            initialized_body = {"jsonrpc": "2.0", "method": "notifications/initialized"}

            with TestClient(http_app) as client:
                init = client.post("/mcp", headers=headers, json=initialize_body)
                initialized = client.post(
                    "/mcp", headers=headers, json=initialized_body
                )

            assert init.status_code == 200
            assert init.json()["error"]["code"] == -32601
            assert init.json()["error"]["message"] == "Method not found: initialize"
            init_headers = {key.lower(): value for key, value in init.headers.items()}
            assert LEGACY_SESSION_HEADER.lower() not in init_headers
            assert not state.session_manager._server_instances

            # Rejecting notifications/initialized and an incoming session id on
            # the modern wire are separate rules, tracked in #27.
            assert initialized.status_code == 202
            assert not state.session_manager._server_instances
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


class TestHeaderTriggeredPing:
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

        @module(name="HeaderPingHttp", controllers=[EchoController])
        class HeaderPingModule:
            pass

        @mcp_app(module=HeaderPingModule, server=ServerConfig(name="header-ping-http"))
        class HeaderPingApp:
            pass

        app = asyncio.run(McpApplicationFactory.create(HeaderPingApp))
        return app.get_combined_app(json_response=True)

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




class TestSep2243AllModernMethods:
    def test_table_covers_modern_surface(self):
        methods = {row.method for row in MODERN_METHOD_CONTRACTS}
        assert "server/discover" in methods
        assert "resources/read" in methods
        assert "prompts/get" in methods
        assert "completion/complete" in methods
        # Tasks are hidden on the 2026 wire until tasks/update exists.
        assert not any(m.startswith("tasks/") for m in methods)
        assert "tools/call" in methods


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
