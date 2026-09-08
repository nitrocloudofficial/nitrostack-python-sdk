"""Tests for MCP 2.0 JSON-RPC wire specification."""

import asyncio
import json

import pytest

from nitrostack.core.errors import ResourceNotFoundError, ToolExecutionError, ValidationError
from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.errors import ERROR_CODE_MESSAGES, JsonRpcErrorCode
from nitrostack.protocol.jsonrpc import (
    HEADER_BODY_MISMATCH,
    PARSE_ERROR,
    HeaderBodyMismatchError,
    InvalidParamsError,
    InvalidRequestError,
    JsonRpcParseError,
    build_tool_error_result,
    map_exception_to_jsonrpc,
    parse_jsonrpc_request,
    validate_header_body_name,
    validate_header_body_method,
    validate_required_mcp_name,
)
from nitrostack.core.context import AuthContext, ExecutionContext
from nitrostack.protocol.meta import (
    bind_request_envelope,
    envelope_identity_is_ignored,
    envelope_protocol_version,
    extract_request_meta,
    split_params_and_meta,
)
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.dispatch import IngressContext, StatelessIngressPipeline


class TestErrorCodes:
    def test_standard_error_codes(self):
        assert int(JsonRpcErrorCode.PARSE_ERROR) == -32700
        assert int(JsonRpcErrorCode.INVALID_REQUEST) == -32600
        assert int(JsonRpcErrorCode.METHOD_NOT_FOUND) == -32601
        assert int(JsonRpcErrorCode.INVALID_PARAMS) == -32602
        assert int(JsonRpcErrorCode.INTERNAL_ERROR) == -32603
        assert int(JsonRpcErrorCode.HEADER_BODY_MISMATCH) == -32020
        assert int(JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION) == -32022
        assert (
            ERROR_CODE_MESSAGES[JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION]
            == "Unsupported protocol version"
        )


class TestMetaEnvelope:
    def test_extract_reverse_dns_keys(self):
        params = {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "Claude Desktop", "version": "1.5.0"},
                "io.modelcontextprotocol/traceparent": "00-abc",
            },
            "name": "demo",
        }
        business, meta = split_params_and_meta(params)
        assert business == {"name": "demo"}
        assert meta.protocol_version == "2026-07-28"
        assert meta.client_info["name"] == "Claude Desktop"
        assert meta.traceparent == "00-abc"

    def test_extract_bare_keys(self):
        meta = extract_request_meta(
            {"_meta": {"traceparent": "00-bare", "clientInfo": {"name": "x"}}}
        )
        assert meta.traceparent == "00-bare"
        assert meta.client_info == {"name": "x"}

    def test_extracts_nested_mcp_protocol_version(self):
        meta = extract_request_meta(
            {"_meta": {"mcp": {"protocolVersion": "2025-06-18"}, "trace": {"id": "t"}}}
        )
        assert meta.protocol_version == "2025-06-18"
        assert envelope_protocol_version(meta) == "2025-06-18"

    def test_parse_request_strips_meta(self):
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "calc",
                    "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
                },
            }
        ).encode()
        req = parse_jsonrpc_request(body)
        assert req.params == {"name": "calc"}
        assert req.meta.protocol_version == "2026-07-28"

    def test_bind_maps_trace_and_header_protocol_version(self):
        envelope = bind_request_envelope(
            raw_meta={"trace": {"id": "span-1"}, "protocolVersion": "2025-06-18"},
            mcp_headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call"},
        )
        assert envelope.meta.trace == {"id": "span-1"}
        assert envelope.protocol_version == "2026-07-28"
        assert envelope.mcp_headers["Mcp-Method"] == "tools/call"

    def test_unsigned_identity_stays_off_context_user(self):
        envelope = bind_request_envelope(
            raw_meta={"userId": "spoofed", "tenantId": "evil", "trace": {"id": "t"}},
        )
        assert envelope.meta.trace == {"id": "t"}
        assert envelope.meta.raw["userId"] == "spoofed"
        assert envelope_identity_is_ignored(envelope.meta.raw) is True

        ctx = ExecutionContext(
            request_id="env-1",
            protocol_version=envelope.protocol_version,
            rpc_meta=envelope.meta,
            mcp_headers=dict(envelope.mcp_headers),
        )
        assert ctx.user is None
        assert ctx.rpc_meta.raw["userId"] == "spoofed"

        ctx.auth = AuthContext(subject="alice")
        assert ctx.user == "alice"

    def test_apply_request_envelope_ignores_spoofed_userid(self):
        from types import SimpleNamespace

        from mcp.shared.context import RequestContext
        from mcp import types

        from nitrostack.core.app import _apply_request_envelope

        rc = RequestContext(
            request_id="1",
            meta=types.RequestParams.Meta.model_validate(
                {
                    "trace": {"id": "span-2"},
                    "userId": "spoofed",
                    "protocolVersion": "2025-11-25",
                }
            ),
            session=None,
            lifespan_context=None,
            request=SimpleNamespace(
                headers={
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Authorization": "Bearer ignore-me",
                }
            ),
        )
        ctx = ExecutionContext(request_id="env-http")
        _apply_request_envelope(ctx, rc)
        assert ctx.protocol_version == "2026-07-28"
        assert ctx.rpc_meta is not None
        assert ctx.rpc_meta.trace == {"id": "span-2"}
        assert ctx.rpc_meta.raw["userId"] == "spoofed"
        assert ctx.user is None
        assert ctx.mcp_headers["MCP-Protocol-Version"] == "2026-07-28"
        assert ctx.mcp_headers["Mcp-Method"] == "tools/call"
        assert "Authorization" not in ctx.mcp_headers
        assert "authorization" not in {key.lower() for key in ctx.mcp_headers}

    def test_apply_request_envelope_sets_verified_jwt_user(self):
        from types import SimpleNamespace

        from mcp.shared.context import RequestContext
        from mcp import types

        from nitrostack.auth.jwt import JWTService
        from nitrostack.core.app import _apply_request_envelope
        from nitrostack.core.di import DIContainer

        DIContainer.reset()
        try:
            jwt = JWTService()
            DIContainer.get_instance().register_value(JWTService, jwt)
            token = jwt.create_token({"sub": "alice", "tenant_id": "acme"})
            rc = RequestContext(
                request_id="1",
                meta=types.RequestParams.Meta.model_validate({"userId": "eve"}),
                session=None,
                lifespan_context=None,
                request=SimpleNamespace(headers={"authorization": f"Bearer {token}"}),
            )
            ctx = ExecutionContext(request_id="env-jwt")
            _apply_request_envelope(ctx, rc)
            assert ctx.user == "alice"
            assert ctx.auth is not None
            assert ctx.auth.claims["tenant_id"] == "acme"
            assert ctx.rpc_meta.raw["userId"] == "eve"
        finally:
            DIContainer.reset()


class TestHeaderBodyMismatch:
    def test_mcp_method_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_header_body_method("tools/list", "tools/call")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH

    def test_mcp_name_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError):
            validate_header_body_name("get_weather", "get_forecast")

    def test_optional_name_allows_missing_header(self):
        validate_header_body_name(None, "echo")

    def test_required_mcp_name_rejects_missing_header(self):
        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_required_mcp_name(None, "echo")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH
        assert "required" in exc.value.message

    def test_required_mcp_name_rejects_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_required_mcp_name("foo", "bar")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH

    def test_required_mcp_name_accepts_exact_match(self):
        validate_required_mcp_name("echo", "echo")

    def test_required_mcp_method_rejects_missing_header(self):
        from nitrostack.protocol.jsonrpc import validate_required_mcp_method

        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_required_mcp_method(None, "tools/call")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH
        assert "required" in exc.value.message

    def test_required_mcp_method_rejects_mismatch(self):
        from nitrostack.protocol.jsonrpc import validate_required_mcp_method

        with pytest.raises(HeaderBodyMismatchError):
            validate_required_mcp_method("tools/list", "tools/call")

    def test_required_mcp_method_accepts_exact_match(self):
        from nitrostack.protocol.jsonrpc import validate_required_mcp_method

        validate_required_mcp_method("tools/call", "tools/call")

    def test_protocol_version_header_meta_mismatch(self):
        from nitrostack.protocol.jsonrpc import validate_protocol_version_header_meta

        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_protocol_version_header_meta("2026-07-28", "2025-06-18")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH

    def test_protocol_version_header_only_is_allowed(self):
        from nitrostack.protocol.jsonrpc import validate_protocol_version_header_meta

        validate_protocol_version_header_meta("2026-07-28", None)
        validate_protocol_version_header_meta(None, "2025-06-18")
        validate_protocol_version_header_meta("2026-07-28", "2026-07-28")

    def test_pipeline_rejects_header_and_meta_mismatch(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "ping",
                    "params": {"_meta": {"mcp": {"protocolVersion": "2025-06-18"}}},
                }
            ).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2026-07-28"}
            )
            assert status == 400
            assert resp["error"]["code"] == HEADER_BODY_MISMATCH

        asyncio.run(_run())

    def test_pipeline_header_only_proceeds(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2026-07-28"}
            )
            assert status == 200
            assert resp["result"] == {}

        asyncio.run(_run())

    def test_pipeline_ignores_unrelated_meta_keys(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "ping",
                    "params": {"_meta": {"trace": {"id": "span"}, "userId": "eve"}},
                }
            ).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2026-07-28"}
            )
            assert status == 200
            assert resp["result"] == {}

        asyncio.run(_run())


class TestUnsupportedProtocolVersion:
    def test_unknown_header_is_rejected(self):
        from nitrostack.protocol.jsonrpc import (
            UNSUPPORTED_PROTOCOL_VERSION,
            UnsupportedProtocolVersionError,
            validate_supported_protocol_version,
        )

        with pytest.raises(UnsupportedProtocolVersionError) as exc:
            validate_supported_protocol_version("1999-01-01", {"2026-07-28"})
        assert int(exc.value.code) == UNSUPPORTED_PROTOCOL_VERSION
        assert str(exc.value) == "Unsupported protocol version"

    def test_pipeline_rejects_unknown_header(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "1999-01-01"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32022
            assert resp["error"]["message"] == "Unsupported protocol version"

        asyncio.run(_run())

    def test_pipeline_accepts_supported_header(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            modern, modern_resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2026-07-28"}
            )
            legacy, legacy_resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2025-06-18"}
            )
            absent, absent_resp = await pipeline.handle_post(body, {})
            assert modern == 200
            assert modern_resp["result"] == {}
            assert legacy == 200
            assert legacy_resp["result"] == {}
            assert absent == 200
            assert absent_resp["result"] == {}

        asyncio.run(_run())

    def test_pipeline_rejects_envelope_only_unknown_version(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="stateless")
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "ping",
                    "params": {"_meta": {"mcp": {"protocolVersion": "1999-01-01"}}},
                }
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 400
            assert resp["error"]["code"] == -32022

        asyncio.run(_run())

    def test_modern_rejects_legacy_dated_header(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION, wire_mode="reject")
            )
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
            status, resp = await pipeline.handle_post(
                body, {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "ping"}
            )
            assert status == 400
            assert resp["error"]["code"] == -32022

        asyncio.run(_run())


class TestToolVsProtocolErrors:
    def test_tool_error_result_uses_is_error_flag(self):
        result = build_tool_error_result("User not found")
        assert result["isError"] is True
        assert result["content"][0]["text"] == "User not found"

    def test_resource_not_found_maps_to_invalid_params(self):
        resp = map_exception_to_jsonrpc(ResourceNotFoundError("missing uri"), "1")
        assert resp["error"]["code"] == int(JsonRpcErrorCode.INVALID_PARAMS)

    def test_validation_error_maps_to_invalid_params(self):
        resp = map_exception_to_jsonrpc(ValidationError("bad schema"), 2)
        assert resp["error"]["code"] == -32602

    def test_tool_execution_error_maps_to_result_not_rpc_error(self):
        resp = map_exception_to_jsonrpc(ToolExecutionError("payment declined"), 3)
        assert "error" not in resp
        assert resp["result"]["isError"] is True


class TestDeprecatedMethods:
    @pytest.mark.parametrize(
        "method",
        ["tasks/result", "tasks/list", "resources/subscribe", "logging/setLevel"],
    )
    def test_deprecated_methods_have_messages(self, method):
        assert deprecated_method_message(method) is not None

    def test_pipeline_rejects_tasks_list(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION)
            )
            body = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tasks/list", "params": {}}
            ).encode()
            status, resp = await pipeline.handle_post(body, {"Mcp-Method": "tasks/list"})
            assert status == 200
            assert resp["error"]["code"] == int(JsonRpcErrorCode.METHOD_NOT_FOUND)

        asyncio.run(_run())


class TestJsonRpcParseErrors:
    def test_invalid_json(self):
        with pytest.raises(JsonRpcParseError) as exc:
            parse_jsonrpc_request(b"{")
        assert int(exc.value.code) == PARSE_ERROR

    def test_non_object_is_invalid_request(self):
        with pytest.raises(InvalidRequestError) as exc:
            parse_jsonrpc_request(b"[]")
        assert int(exc.value.code) == int(JsonRpcErrorCode.INVALID_REQUEST)

    def test_wrong_jsonrpc_version_is_invalid_request(self):
        with pytest.raises(InvalidRequestError):
            parse_jsonrpc_request(b'{"jsonrpc":"1.0","id":1,"method":"ping"}')

    def test_missing_method_is_invalid_request(self):
        with pytest.raises(InvalidRequestError):
            parse_jsonrpc_request(b'{"jsonrpc":"2.0","id":1}')

    def test_wire_error_to_response(self):
        err = InvalidParamsError("amount must be positive", data={"param": "amount"})
        resp = err.to_response("req-12345")
        assert resp["id"] == "req-12345"
        assert resp["error"]["code"] == -32602
        assert resp["error"]["data"]["param"] == "amount"
