"""JSON-RPC 2.0 wire protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from nitrostack.protocol.errors import JsonRpcErrorCode, ERROR_CODE_MESSAGES
from nitrostack.protocol.meta import RequestMeta, split_params_and_meta

JSONRPC_VERSION = "2.0"

# Backward-compatible aliases
PARSE_ERROR = int(JsonRpcErrorCode.PARSE_ERROR)
HEADER_BODY_MISMATCH = int(JsonRpcErrorCode.HEADER_BODY_MISMATCH)


@dataclass(frozen=True)
class JsonRpcRequest:
    """Parsed JSON-RPC 2.0 request object."""

    id: Any
    method: str
    params: dict[str, Any]
    meta: RequestMeta


class JsonRpcWireError(Exception):
    """Base class for JSON-RPC wire failures."""

    code: JsonRpcErrorCode
    message: str
    data: Any = None

    def __init__(
        self,
        code: JsonRpcErrorCode,
        message: Optional[str] = None,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_CODE_MESSAGES.get(code, "Error")
        self.data = data
        super().__init__(self.message)

    def to_response(self, request_id: Any) -> dict[str, Any]:
        return jsonrpc_error(request_id, int(self.code), self.message, self.data)


class JsonRpcParseError(JsonRpcWireError):
    def __init__(self, message: str = "Parse error") -> None:
        super().__init__(JsonRpcErrorCode.PARSE_ERROR, message)


class InvalidRequestError(JsonRpcWireError):
    def __init__(self, message: str = "Invalid Request", data: Any = None) -> None:
        super().__init__(JsonRpcErrorCode.INVALID_REQUEST, message, data)


class MethodNotFoundError(JsonRpcWireError):
    def __init__(self, method: str) -> None:
        super().__init__(
            JsonRpcErrorCode.METHOD_NOT_FOUND,
            f"Method not found: {method}",
        )


class InvalidParamsError(JsonRpcWireError):
    def __init__(self, message: str = "Invalid params", data: Any = None) -> None:
        super().__init__(JsonRpcErrorCode.INVALID_PARAMS, message, data)


class InternalError(JsonRpcWireError):
    def __init__(self, message: str = "Internal error", data: Any = None) -> None:
        super().__init__(JsonRpcErrorCode.INTERNAL_ERROR, message, data)


class HeaderBodyMismatchError(JsonRpcWireError):
    def __init__(self, message: str = "Header/body mismatch") -> None:
        super().__init__(JsonRpcErrorCode.HEADER_BODY_MISMATCH, message)


def parse_jsonrpc_request(raw_body: bytes) -> JsonRpcRequest:
    """Parse and validate a JSON-RPC 2.0 request from HTTP POST body."""
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JsonRpcParseError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise JsonRpcParseError("Request must be a JSON object")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcParseError("jsonrpc must be '2.0'")
    if "method" not in payload or not isinstance(payload["method"], str):
        raise JsonRpcParseError("method is required and must be a string")

    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise JsonRpcParseError("params must be an object when present")

    business_params, meta = split_params_and_meta(params)

    return JsonRpcRequest(
        id=payload.get("id"),
        method=payload["method"],
        params=business_params,
        meta=meta,
    )


def validate_header_body_method(header_method: Optional[str], body_method: str) -> None:
    """SEP-2243: reject when Mcp-Method header mirrors a different JSON-RPC method."""
    if header_method is None:
        return
    if header_method != body_method:
        raise HeaderBodyMismatchError(
            f"Mcp-Method header '{header_method}' does not match body method '{body_method}'"
        )


def validate_header_body_name(
    header_name: Optional[str],
    body_name: Optional[str],
) -> None:
    """SEP-2243: reject when Mcp-Name header mirrors a different resource/tool name."""
    if header_name is None or body_name is None:
        return
    if header_name != body_name:
        raise HeaderBodyMismatchError(
            f"Mcp-Name header '{header_name}' does not match body name '{body_name}'"
        )


def jsonrpc_success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def build_ping_response(request_id: Any) -> dict[str, Any]:
    """Ping fast path."""
    return jsonrpc_success(request_id, {})


def build_tool_error_result(message: str, *, text_type: str = "text") -> dict[str, Any]:
    """
    Tool business failure — JSON-RPC success with isError: true.
    NOT a top-level JSON-RPC error response.
    """
    return {
        "content": [{"type": text_type, "text": message}],
        "isError": True,
    }


def wrap_tool_success_result(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Standard successful tool result payload."""
    return {"content": content, "isError": False}


def map_exception_to_jsonrpc(exc: Exception, request_id: Any) -> dict[str, Any]:
    """Map SDK exceptions to JSON-RPC wire responses."""
    if isinstance(exc, JsonRpcWireError):
        return exc.to_response(request_id)

    from nitrostack.core.errors import (
        PromptNotFoundError,
        ResourceNotFoundError,
        ToolExecutionError,
        ValidationError,
    )

    if isinstance(exc, (ResourceNotFoundError, PromptNotFoundError, ValidationError)):
        # SEP-2164: missing resources return -32602
        return InvalidParamsError(str(exc)).to_response(request_id)

    if isinstance(exc, ToolExecutionError):
        return jsonrpc_success(request_id, build_tool_error_result(str(exc)))

    return InternalError(str(exc)).to_response(request_id)
