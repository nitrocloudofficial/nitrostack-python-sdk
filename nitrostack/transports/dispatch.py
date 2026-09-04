"""Stateless HTTP ingress pipeline (Doc 01 §3)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.jsonrpc import (
    HeaderBodyMismatchError,
    JsonRpcParseError,
    JsonRpcRequest,
    JsonRpcWireError,
    build_ping_response,
    jsonrpc_error,
    jsonrpc_success,
    parse_jsonrpc_request,
    validate_header_body_method,
    validate_header_body_name,
)
from nitrostack.transports.headers import HEADER_MCP_METHOD, HEADER_MCP_NAME, get_header

TaskDispatchHandler = Callable[[JsonRpcRequest], Awaitable[Optional[dict[str, Any]]]]
RegistryDispatchHandler = Callable[[JsonRpcRequest], Awaitable[Optional[dict[str, Any]]]]


class DispatchStage(str, Enum):
    """Six-step ingress lifecycle from Doc 01 §3."""

    CORS_SECURITY = "cors_security"
    BODY_PARSING = "body_parsing"
    PING_FAST_PATH = "ping_fast_path"
    TASK_INTERCEPTION = "task_interception"
    REGISTRY_DISPATCH = "registry_dispatch"
    RESPONSE_SERIALIZATION = "response_serialization"


TASK_METHOD_PREFIX = "tasks/"


@dataclass
class IngressContext:
    server_name: str
    server_version: str
    protocol_version: str
    advertise_tasks: bool = True
    advertise_app: bool = False


def is_task_wire_interception(method: str, params: dict[str, Any]) -> bool:
    """Doc 01 §3 step 4 — route to task subsystem when matched."""
    if method.startswith(TASK_METHOD_PREFIX):
        return True
    return method == "tools/call" and bool(params.get("task"))


class StatelessIngressPipeline:
    """
    Deterministic JSON-RPC pre-dispatch for stateless POST /mcp.

    Handles ping and server/discover inline. Task and registry methods return
    None so the underlying MCP server can handle them (Doc 05+ adds task handler).
    """

    def __init__(
        self,
        context: IngressContext,
        *,
        task_handler: Optional[TaskDispatchHandler] = None,
        registry_handler: Optional[RegistryDispatchHandler] = None,
    ) -> None:
        self._context = context
        self._task_handler = task_handler
        self._registry_handler = registry_handler

    async def handle_post(
        self,
        raw_body: bytes,
        request_headers: dict[str, str],
    ) -> Optional[tuple[int, dict[str, Any]]]:
        """
        Run ingress steps 2–5. Returns None to delegate to the underlying MCP app.
        Step 1 (CORS) is handled by transport middleware.
        """
        try:
            request = parse_jsonrpc_request(raw_body)
        except JsonRpcParseError as exc:
            return 400, exc.to_response(None)
        except JsonRpcWireError as exc:
            return 400, exc.to_response(None)

        header_method = get_header(request_headers, HEADER_MCP_METHOD)
        try:
            validate_header_body_method(header_method, request.method)
        except HeaderBodyMismatchError as exc:
            return 400, exc.to_response(request.id)

        header_name = get_header(request_headers, HEADER_MCP_NAME)
        body_name = request.params.get("name") or request.params.get("uri")
        if isinstance(body_name, str):
            try:
                validate_header_body_name(header_name, body_name)
            except HeaderBodyMismatchError as exc:
                return 400, exc.to_response(request.id)

        deprecated_msg = deprecated_method_message(request.method)
        if deprecated_msg is not None:
            return 200, jsonrpc_error(
                request.id,
                int(JsonRpcErrorCode.METHOD_NOT_FOUND),
                deprecated_msg,
            )

        if request.method == "ping":
            return 200, build_ping_response(request.id)

        if request.method == "server/discover":
            result = build_discover_result(
                server_name=self._context.server_name,
                server_version=self._context.server_version,
                protocol_version=self._context.protocol_version,
                advertise_tasks=self._context.advertise_tasks,
                advertise_app=self._context.advertise_app,
            )
            return 200, jsonrpc_success(request.id, result)

        if is_task_wire_interception(request.method, request.params):
            if self._task_handler is not None:
                response = await self._task_handler(request)
                if response is not None:
                    return 200, response
            return None

        if self._registry_handler is not None:
            response = await self._registry_handler(request)
            if response is not None:
                return 200, response

        return None

    @staticmethod
    def serialize_response(jsonrpc_response: dict[str, Any]) -> bytes:
        """Step 6 — JSON-RPC response serialization."""
        return json.dumps(jsonrpc_response).encode("utf-8")
