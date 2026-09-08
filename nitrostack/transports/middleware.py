"""Stateless HTTP ASGI middleware."""

from __future__ import annotations

from typing import Any, Callable, Optional

from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.jsonrpc import jsonrpc_error, jsonrpc_method_from_body
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION, ProtocolEra, WireMode
from nitrostack.runtime.stateless import assert_stateless_headers
from nitrostack.transports.cors import build_cors_headers, cors_preflight_response_headers
from nitrostack.transports.dispatch import (
    DiscoverHandler,
    IngressContext,
    InitializeHandler,
    StatelessIngressPipeline,
)
from nitrostack.transports.headers import (
    MCP_HTTP_PATH,
    build_mcp_echo_headers,
    get_header,
    scope_without_session_headers,
    strip_legacy_session_headers,
)

ASGIApp = Callable[..., Any]

MCP_POST_PATHS = (MCP_HTTP_PATH, f"{MCP_HTTP_PATH}/")


class StatelessTransportMiddleware:
    """
    ASGI wrapper implementing stateless HTTP transport invariants:
    - OPTIONS 204 CORS preflight on MCP paths only
    - Legacy session header stripping
    - MCP response headers on all responses
    - Pre-dispatch for POST /mcp (ping, server/discover)
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        pipeline: Optional[StatelessIngressPipeline] = None,
        mcp_paths: tuple[str, ...] = MCP_POST_PATHS,
        enable_cors: bool = True,
    ) -> None:
        self.app = app
        self.pipeline = pipeline
        self.mcp_paths = mcp_paths
        self.enable_cors = enable_cors

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")

        if method == "OPTIONS" and path in self.mcp_paths and self.enable_cors:
            await self._send_options(scope, receive, send)
            return

        buffered_body: Optional[bytes] = None
        if method == "POST" and path in self.mcp_paths and self.pipeline is not None:
            buffered_body = await self._read_body(receive)
            handled = await self._try_pre_dispatch(scope, buffered_body, send)
            if handled:
                return
            raw_headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in (scope.get("headers") or [])
            }
            method_rejected = self.pipeline.reject_jsonrpc_mcp_method(
                buffered_body, raw_headers
            )
            if method_rejected is not None:
                await self._send_pipeline_response(
                    scope, send, raw_headers, method_rejected, body=buffered_body
                )
                return
            name_rejected = self.pipeline.reject_tools_call_mcp_name(
                buffered_body, raw_headers
            )
            if name_rejected is not None:
                await self._send_pipeline_response(
                    scope, send, raw_headers, name_rejected, body=buffered_body
                )
                return
            version_rejected = self.pipeline.reject_protocol_version_cross_check(
                buffered_body, raw_headers
            )
            if version_rejected is not None:
                await self._send_pipeline_response(
                    scope, send, raw_headers, version_rejected, body=buffered_body
                )
                return
            unsupported = self.pipeline.reject_unsupported_protocol_version_header(
                buffered_body, raw_headers
            )
            if unsupported is not None:
                await self._send_pipeline_response(
                    scope, send, raw_headers, unsupported, body=buffered_body
                )
                return
            receive = self._replay_receive(buffered_body, receive)

        if path in self.mcp_paths and self.pipeline is not None:
            raw_headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in (scope.get("headers") or [])
            }
            if self.pipeline.forbids_incoming_session_id(raw_headers):
                await self._send_session_id_rejected(scope, send, raw_headers)
                return

        await self._forward_with_stateless_headers(
            scope, receive, send, body=buffered_body
        )

    async def _send_options(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers_list = scope.get("headers") or []
        req_headers = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in headers_list
        }
        cors = cors_preflight_response_headers(req_headers)
        response_headers = self._echo_headers(req_headers, extra=cors)
        assert_stateless_headers(response_headers)

        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": self._encode_headers(response_headers),
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _try_pre_dispatch(
        self,
        scope: dict[str, Any],
        body: bytes,
        send: Any,
    ) -> bool:
        headers_list = scope.get("headers") or []
        raw_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in headers_list}

        assert self.pipeline is not None
        result = await self.pipeline.handle_post(body, raw_headers)
        if result is None:
            return False

        await self._send_pipeline_response(scope, send, raw_headers, result, body=body)
        return True

    async def _send_pipeline_response(
        self,
        scope: dict[str, Any],
        send: Any,
        raw_headers: dict[str, str],
        result: tuple[int, dict[str, Any]],
        *,
        body: Optional[bytes] = None,
    ) -> None:
        status, jsonrpc_response = result
        origin = get_header(raw_headers, "Origin")
        cors = build_cors_headers(origin=origin)
        response_headers = self._echo_headers(raw_headers, extra=cors, body=body)
        assert_stateless_headers(response_headers)
        assert self.pipeline is not None
        payload = self.pipeline.serialize_response(jsonrpc_response)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": self._encode_headers(response_headers),
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def _send_session_id_rejected(
        self,
        scope: dict[str, Any],
        send: Any,
        raw_headers: dict[str, str],
    ) -> None:
        origin = get_header(raw_headers, "Origin")
        cors = build_cors_headers(origin=origin)
        response_headers = self._echo_headers(raw_headers, extra=cors)
        assert_stateless_headers(response_headers)
        payload = StatelessIngressPipeline.serialize_response(
            jsonrpc_error(
                None,
                int(JsonRpcErrorCode.INVALID_REQUEST),
                "Invalid Request: Mcp-Session-Id is not supported",
            )
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": self._encode_headers(response_headers),
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def _forward_with_stateless_headers(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        *,
        body: Optional[bytes] = None,
    ) -> None:
        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw_headers = {
                    k.decode("latin-1"): v.decode("latin-1")
                    for k, v in message.get("headers", [])
                }
                req_headers = {
                    k.decode("latin-1"): v.decode("latin-1")
                    for k, v in (scope.get("headers") or [])
                }
                merged = self._echo_headers(
                    req_headers,
                    content_type=raw_headers.get("content-type", "application/json"),
                    extra={
                        **strip_legacy_session_headers(raw_headers),
                        **build_cors_headers(origin=get_header(req_headers, "Origin")),
                    },
                    body=body,
                )
                assert_stateless_headers(merged)
                message = {
                    **message,
                    "headers": self._encode_headers(merged),
                }
            await send(message)

        await self.app(scope_without_session_headers(scope), receive, send_wrapper)

    def _echo_headers(
        self,
        request_headers: dict[str, str],
        *,
        extra: Optional[dict[str, str]] = None,
        content_type: str = "application/json",
        body: Optional[bytes] = None,
    ) -> dict[str, str]:
        fallback = MODERN_PROTOCOL_VERSION
        supported = None
        if self.pipeline is not None:
            fallback = self.pipeline.response_protocol_version()
            supported = self.pipeline.response_supported_versions()
        return build_mcp_echo_headers(
            request_headers,
            protocol_version=fallback,
            method=jsonrpc_method_from_body(body),
            supported_versions=supported,
            content_type=content_type,
            extra=extra,
        )

    @staticmethod
    async def _read_body(receive: Any) -> bytes:
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        return body

    @staticmethod
    def _replay_receive(body: bytes, original_receive: Any) -> Any:
        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Body was already buffered. Wait for a real client disconnect
            # instead of synthesizing one — Streamable HTTP treats disconnect
            # as an abort of the in-flight request.
            while True:
                message = await original_receive()
                if message.get("type") == "http.disconnect":
                    return message

        return replay

    @staticmethod
    def _encode_headers(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
        return [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]


def wrap_stateless_transport(
    app: ASGIApp,
    *,
    server_name: str,
    server_version: str,
    protocol_version: str,
    advertise_tasks: bool = True,
    advertise_app: bool = False,
    custom_extensions: Optional[dict[str, str]] = None,
    wire_mode: WireMode = "stateless",
    protocol_era: Optional[ProtocolEra] = None,
    enable_cors: bool = True,
    discover_handler: Optional[DiscoverHandler] = None,
    initialize_handler: Optional[InitializeHandler] = None,
) -> ASGIApp:
    """Wrap an ASGI app with stateless HTTP middleware."""
    pipeline = StatelessIngressPipeline(
        IngressContext(
            server_name=server_name,
            server_version=server_version,
            protocol_version=protocol_version,
            advertise_tasks=advertise_tasks,
            advertise_app=advertise_app,
            custom_extensions=custom_extensions,
            wire_mode=wire_mode,
            protocol_era=protocol_era,
        ),
        discover_handler=discover_handler,
        initialize_handler=initialize_handler,
    )
    return StatelessTransportMiddleware(app, pipeline=pipeline, enable_cors=enable_cors)
