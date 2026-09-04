"""Minimal JSON-RPC helpers for stateless HTTP ingress (Doc 01 §3; full spec in Doc 02)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

PARSE_ERROR = -32700
HEADER_BODY_MISMATCH = -32020
JSONRPC_VERSION = "2.0"


@dataclass(frozen=True)
class JsonRpcRequest:
    """Parsed JSON-RPC 2.0 request object."""

    id: Any
    method: str
    params: dict[str, Any]


class JsonRpcParseError(Exception):
    """Invalid JSON or malformed JSON-RPC request (-32700)."""

    def __init__(self, message: str = "Parse error") -> None:
        super().__init__(message)
        self.code = PARSE_ERROR


class HeaderBodyMismatchError(Exception):
    """Mcp-Method header does not match JSON body method (-32020, SEP-2243)."""

    def __init__(self, message: str = "Header/body mismatch") -> None:
        super().__init__(message)
        self.code = HEADER_BODY_MISMATCH


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

    return JsonRpcRequest(
        id=payload.get("id"),
        method=payload["method"],
        params=params,
    )


def validate_header_body_method(header_method: Optional[str], body_method: str) -> None:
    """SEP-2243: reject when Mcp-Method header mirrors a different JSON-RPC method."""
    if header_method is None:
        return
    if header_method != body_method:
        raise HeaderBodyMismatchError(
            f"Mcp-Method header '{header_method}' does not match body method '{body_method}'"
        )


def jsonrpc_success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def build_ping_response(request_id: Any) -> dict[str, Any]:
    """Ping fast path (Doc 01 §3 step 3)."""
    return jsonrpc_success(request_id, {})
