"""Standard JSON-RPC error codes for MCP 2026-07-28."""

from __future__ import annotations

from enum import IntEnum


class JsonRpcErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    HEADER_BODY_MISMATCH = -32020


ERROR_CODE_MESSAGES: dict[JsonRpcErrorCode, str] = {
    JsonRpcErrorCode.PARSE_ERROR: "Parse error",
    JsonRpcErrorCode.INVALID_REQUEST: "Invalid Request",
    JsonRpcErrorCode.METHOD_NOT_FOUND: "Method not found",
    JsonRpcErrorCode.INVALID_PARAMS: "Invalid params",
    JsonRpcErrorCode.INTERNAL_ERROR: "Internal error",
    JsonRpcErrorCode.HEADER_BODY_MISMATCH: "Header/body mismatch",
}
