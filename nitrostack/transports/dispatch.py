"""Stateless HTTP ingress pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.errors import ERROR_CODE_MESSAGES, JsonRpcErrorCode
from nitrostack.protocol.jsonrpc import (
    HeaderBodyMismatchError,
    JsonRpcParseError,
    JsonRpcRequest,
    JsonRpcWireError,
    MethodNotFoundError,
    build_ping_response,
    jsonrpc_error,
    jsonrpc_success,
    parse_jsonrpc_request,
    validate_header_body_method,
    validate_header_body_name,
)
from nitrostack.protocol.version import LEGACY_PROTOCOL_VERSION, WireMode
from nitrostack.transports.headers import (
    HEADER_MCP_METHOD,
    HEADER_MCP_NAME,
    HEADER_MCP_PROTOCOL_VERSION,
    get_header,
)

TaskDispatchHandler = Callable[[JsonRpcRequest], Awaitable[Optional[dict[str, Any]]]]
RegistryDispatchHandler = Callable[[JsonRpcRequest], Awaitable[Optional[dict[str, Any]]]]


class DispatchStage(str, Enum):
    """Six-step stateless HTTP ingress lifecycle."""

    CORS_SECURITY = "cors_security"
    BODY_PARSING = "body_parsing"
    PING_FAST_PATH = "ping_fast_path"
    TASK_INTERCEPTION = "task_interception"
    REGISTRY_DISPATCH = "registry_dispatch"
    RESPONSE_SERIALIZATION = "response_serialization"


TASK_METHOD_PREFIX = "tasks/"
LEGACY_HANDSHAKE_METHODS = frozenset({"initialize", "notifications/initialized"})


@dataclass
class IngressContext:
    server_name: str
    server_version: str
    protocol_version: str
    advertise_tasks: bool = True
    advertise_app: bool = False
    custom_extensions: Optional[dict[str, str]] = None
    wire_mode: WireMode = "stateless"


def reject_legacy_wire(
    request: JsonRpcRequest,
    request_headers: dict[str, str],
    wire_mode: WireMode,
) -> Optional[tuple[int, dict[str, Any]]]:
    """
    Era ``modern`` (``wire_mode=reject``) fails closed on 2025-shaped traffic.

    Official v2 ``legacy: 'reject'`` is not mounted yet; this is the sidecar
    stand-in. ``auto`` keeps ``wire_mode=stateless`` and does not use this path.
    """
    if wire_mode != "reject":
        return None

    if get_header(request_headers, LEGACY_SESSION_HEADER):
        return 400, jsonrpc_error(
            request.id,
            int(JsonRpcErrorCode.INVALID_REQUEST),
            "Invalid Request: Mcp-Session-Id is not supported",
        )

    header_version = get_header(request_headers, HEADER_MCP_PROTOCOL_VERSION)
    body_version = request.params.get("protocolVersion")
    if header_version == LEGACY_PROTOCOL_VERSION or body_version == LEGACY_PROTOCOL_VERSION:
        return 400, jsonrpc_error(
            request.id,
            int(JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION),
            ERROR_CODE_MESSAGES[JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION],
        )

    if request.method in LEGACY_HANDSHAKE_METHODS:
        return 200, MethodNotFoundError(request.method).to_response(request.id)

    return None


def is_task_wire_interception(method: str, params: dict[str, Any]) -> bool:
    """Ingress step 4 — route to the task subsystem when matched."""
    if method.startswith(TASK_METHOD_PREFIX):
        return True
    return method == "tools/call" and bool(params.get("task"))


class StatelessIngressPipeline:
    """
    Deterministic JSON-RPC pre-dispatch for stateless POST /mcp.

    Handles ping, server/discover, and deprecated-method rejection inline.
    Task and tool methods always return None so ``TaskManager`` plus the
    low-level MCP server remain the only production task path.
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

        rejected = reject_legacy_wire(request, request_headers, self._context.wire_mode)
        if rejected is not None:
            return rejected

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
                custom_extensions=self._context.custom_extensions,
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
