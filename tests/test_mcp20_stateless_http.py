"""Tests for MCP 2.0 stateless HTTP transport."""

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
    parse_jsonrpc_request,
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
from tests.http_probe import decode, envelope, headers, http_client, post


@pytest.mark.parametrize("era", ["legacy", "auto", "modern"])
@pytest.mark.parametrize("json_response", [True, False])
def test_official_transport_tool_and_resource_roundtrip(era, json_response):
    version = "2025-06-18" if era == "legacy" else MODERN_PROTOCOL_VERSION
    with http_client(era, json_response=json_response) as (_, http, client):
        assert http.state.streamable_http_manager_count == 1
        assert http.state.sessionful == (era == "legacy")
        response = post(client, "tools/call", {"name": "echo", "arguments": {"value": "wire"}},
                        modern=era != "legacy", version=version)
        assert response.status_code == 200, response.text
        assert decode(response)["result"]["structuredContent"]["value"] == "wire"
        response = post(client, "resources/read", {"uri": "probe://info"},
                        modern=era != "legacy", version=version)
        assert response.status_code == 200, response.text
        assert decode(response)["result"]["contents"][0]["text"] == "probe resource"
        if era != "legacy":
            assert "mcp-session-id" not in response.headers


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("body", [b"", b"not-json", b"{}"])
def test_header_only_ping_cannot_synthesize_a_success(era, body):
    with http_client(era) as (_, _, client):
        response = client.post("/mcp", content=body, headers=headers("ping"))
        assert response.status_code == 400, response.text
        assert "result" not in decode(response)


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_unknown_method_is_answered_by_official_dispatch(era):
    with http_client(era) as (_, _, client):
        response = post(client, "unknown/probe")
        assert response.status_code == 404, response.text
        assert decode(response)["error"]["code"] == -32601


@pytest.mark.parametrize("era", ["legacy", "auto", "modern"])
def test_http_health_version_slash_and_era(era):
    with http_client(era) as (_, http, client):
        health = client.get("/mcp/health")
        assert health.status_code == 200
        assert http.state.protocol_era == era
        version = client.get("/json/version").json()["Protocol-Version"]
        assert version == ("2025-06-18" if era == "legacy" else MODERN_PROTOCOL_VERSION)
        response = client.post("/mcp/", json=envelope(), headers=headers())
        if era != "legacy":
            assert response.status_code == 200, response.text
            assert decode(response)["result"]["tools"][0]["name"] == "echo"


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_allowlisted_cors_applies_once_to_success_and_errors(era, monkeypatch):
    monkeypatch.setenv("MCP_CORS_ALLOWED_ORIGINS", "https://allowed.example")
    with http_client(era) as (_, _, client):
        for method in ["tools/list", "unknown/probe"]:
            response = post(client, method, extra_headers={"Origin": "https://allowed.example"})
            assert response.headers.get_list("access-control-allow-origin") == ["https://allowed.example"]
        denied = post(client, extra_headers={"Origin": "https://denied.example"})
        assert "access-control-allow-origin" not in denied.headers
        preflight = client.options("/mcp", headers={"Origin": "https://allowed.example",
                                                    "Access-Control-Request-Method": "POST"})
        assert preflight.status_code == 200
        assert preflight.headers.get_list("access-control-allow-origin") == ["https://allowed.example"]


def test_verified_http_identity_and_argument_header_mirroring():
    from nitrostack.auth.jwt import JWTService
    from nitrostack.core.di import DIContainer

    with http_client("auto") as (_, _, client):
        jwt = JWTService()
        DIContainer.get_instance().register_value(JWTService, jwt)
        token = jwt.create_token({"sub": "alice", "tenant_id": "one"})
        response = post(client, "tools/call", {
            "name": "echo", "arguments": {}, "_meta": {"userId": "mallory"},
        }, extra_headers={"Authorization": "Bearer " + token, "Mcp-Param-value": "from-header"})
        result = decode(response)["result"]["structuredContent"]
        assert result["value"] == "from-header"
        assert result["mirrored"] == {"value": "from-header"}
        assert result["user"] == "alice"
        assert result["protocol_version"] == MODERN_PROTOCOL_VERSION
        kept = post(client, "tools/call", {"name": "echo", "arguments": {"value": "from-body"}},
                    extra_headers={"Mcp-Param-value": "from-header"})
        assert decode(kept)["result"]["structuredContent"]["value"] == "from-body"


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_incoming_session_id_cannot_attach_sessionless_calls(era):
    with http_client(era) as (_, _, client):
        first = post(client, "tools/call", {"name": "echo", "arguments": {"value": "first"}},
                     extra_headers={"Mcp-Session-Id": "forged"})
        second = post(client, "tools/call", {"name": "echo", "arguments": {"value": "second"}},
                      extra_headers={"Mcp-Session-Id": "forged"})
        for response, expected in [(first, "first"), (second, "second")]:
            assert response.status_code == 200, response.text
            assert "mcp-session-id" not in response.headers
            assert decode(response)["result"]["structuredContent"]["value"] == expected



def test_response_echo_uses_supported_request_version():
    echoed = build_mcp_echo_headers(
        {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "tools/call"},
        protocol_version=MODERN_PROTOCOL_VERSION,
        supported_versions={MODERN_PROTOCOL_VERSION, "2025-06-18"},
    )
    assert echoed["MCP-Protocol-Version"] == "2025-06-18"
    assert echoed["Mcp-Method"] == "tools/call"
    assert handled_protocol_version(
        {"MCP-Protocol-Version": "1999-01-01"}, fallback=MODERN_PROTOCOL_VERSION,
        supported={MODERN_PROTOCOL_VERSION, "2025-06-18"},
    ) == MODERN_PROTOCOL_VERSION


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
        assert "io.modelcontextprotocol/tasks" not in result["capabilities"].get("extensions", {})
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
