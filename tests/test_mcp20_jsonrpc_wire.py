"""Tests for MCP 2.0 JSON-RPC wire specification."""

import asyncio
import json

import pytest

from nitrostack.core.errors import ResourceNotFoundError, ToolExecutionError, ValidationError
from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.errors import JsonRpcErrorCode
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
)
from nitrostack.protocol.meta import extract_request_meta, split_params_and_meta
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


class TestHeaderBodyMismatch:
    def test_mcp_method_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError) as exc:
            validate_header_body_method("tools/list", "tools/call")
        assert int(exc.value.code) == HEADER_BODY_MISMATCH

    def test_mcp_name_mismatch(self):
        with pytest.raises(HeaderBodyMismatchError):
            validate_header_body_name("get_weather", "get_forecast")


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
            status, resp = await pipeline.handle_post(body, {})
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
