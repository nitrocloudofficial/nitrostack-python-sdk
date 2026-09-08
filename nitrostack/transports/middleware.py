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
    decode_asgi_headers,
    get_header,
    scope_with_header_snapshot,
    snapshot_validated_asgi_headers,
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
        header_snapshot: Optional[tuple[tuple[bytes, bytes], ...]] = None
        if method == "POST" and path in self.mcp_paths and self.pipeline is not None:
            buffered_body = await self._read_body(receive)
            handled = await self._try_pre_dispatch(scope, buffered_body, send)
            if handled:
                return
            live_headers = decode_asgi_headers(list(scope.get("headers") or []))
            rejected = self._reject_replay_headers(buffered_body, live_headers)
            if rejected is not None:
                await self._send_pipeline_response(
                    scope, send, live_headers, rejected, body=buffered_body
                )
                return
            if self.pipeline.forbids_incoming_session_id(live_headers):
                await self._send_session_id_rejected(scope, send, live_headers)
                return
            header_snapshot = snapshot_validated_asgi_headers(list(scope.get("headers") or []))
            snapshot_headers = decode_asgi_headers(list(header_snapshot))
            rejected = self._reject_replay_headers(buffered_body, snapshot_headers)
            if rejected is not None:
                await self._send_pipeline_response(
                    scope, send, snapshot_headers, rejected, body=buffered_body
                )
                return
            receive = self._replay_receive(buffered_body, receive)
            scope = scope_with_header_snapshot(scope, header_snapshot)

        if path in self.mcp_paths and self.pipeline is not None:
            raw_headers = decode_asgi_headers(list(scope.get("headers") or []))
            if method == "GET":
                rejected = self.pipeline.reject_method_policy(b"", raw_headers)
                if rejected is not None:
                    await self._send_pipeline_response(
                        scope, send, raw_headers, rejected, body=b""
                    )
                    return
            if self.pipeline.forbids_incoming_session_id(raw_headers):
                await self._send_session_id_rejected(scope, send, raw_headers)
                return

        await self._forward_with_stateless_headers(
            scope, receive, send, body=buffered_body
        )

    def _reject_replay_headers(
        self,
        raw_body: bytes,
        request_headers: dict[str, str],
    ) -> Optional[tuple[int, dict[str, Any]]]:
        """Re-apply method policy, ``-32020``, and ``-32022`` on replay."""
        assert self.pipeline is not None
        policy_rejected = self.pipeline.reject_method_policy(raw_body, request_headers)
        if policy_rejected is not None:
            return policy_rejected
        method_rejected = self.pipeline.reject_jsonrpc_mcp_method(raw_body, request_headers)
        if method_rejected is not None:
            return method_rejected
        name_rejected = self.pipeline.reject_tools_call_mcp_name(raw_body, request_headers)
        if name_rejected is not None:
            return name_rejected
        version_rejected = self.pipeline.reject_protocol_version_cross_check(
            raw_body, request_headers
        )
        if version_rejected is not None:
            return version_rejected
        return self.pipeline.reject_unsupported_protocol_version_header(
            raw_body, request_headers
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

        await self.app(
            scope_with_header_snapshot(
                scope,
                snapshot_validated_asgi_headers(list(scope.get("headers") or [])),
            ),
            receive,
            send_wrapper,
        )

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


class SessionlessHttpGuard:
    """
    Transport invariants official mcp 2.x does not own on sessionless ``/mcp``.

    Does not parse or answer ``tools/call``. Forwards those to the v2 app.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        protocol_era: ProtocolEra = "auto",
        enable_cors: bool = True,
        discover_handler: Optional[DiscoverHandler] = None,
    ) -> None:
        self.app = app
        self.protocol_era = protocol_era
        self.enable_cors = enable_cors
        self.discover_handler = discover_handler
        wire_mode: WireMode = "reject" if protocol_era == "modern" else "stateless"
        self._sender = StatelessTransportMiddleware(
            app,
            pipeline=StatelessIngressPipeline(
                IngressContext(
                    server_name="",
                    server_version="",
                    protocol_version=MODERN_PROTOCOL_VERSION,
                    wire_mode=wire_mode,
                    protocol_era=protocol_era,
                ),
                discover_handler=discover_handler,
            ),
            enable_cors=enable_cors,
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "").upper()
        path = scope.get("path", "")
        if method == "OPTIONS" and path in MCP_POST_PATHS and self.enable_cors:
            await self._sender._send_options(scope, receive, send)
            return
        raw_headers = decode_asgi_headers(list(scope.get("headers") or []))
        from nitrostack.runtime.stateless import has_incoming_session_id
        from nitrostack.transports.dispatch import (
            is_header_only_ping,
            reject_modern_method_policy,
        )
        from nitrostack.transports.headers import HEADER_MCP_METHOD, get_header

        if path in MCP_POST_PATHS and has_incoming_session_id(raw_headers):
            await self._sender._send_session_id_rejected(scope, send, raw_headers)
            return
        if path in MCP_POST_PATHS and method == "GET":
            header_method = get_header(raw_headers, HEADER_MCP_METHOD)
            if header_method is not None:
                rejected = reject_modern_method_policy(
                    header_method.strip(), None, self.protocol_era
                )
                if rejected is not None:
                    await self._sender._send_pipeline_response(
                        scope, send, raw_headers, rejected, body=b""
                    )
                    return
        if method != "POST" or path not in MCP_POST_PATHS:
            await self.app(scope, receive, send)
            return

        body = await StatelessTransportMiddleware._read_body(receive)
        if is_header_only_ping(body, raw_headers):
            from nitrostack.protocol.jsonrpc import build_ping_response

            await self._sender._send_pipeline_response(
                scope, send, raw_headers, (200, build_ping_response(None)), body=body
            )
            return

        from nitrostack.protocol.jsonrpc import (
            JsonRpcParseError,
            JsonRpcWireError,
            parse_jsonrpc_request,
            validate_header_body_method,
        )
        try:
            request = parse_jsonrpc_request(body)
        except (JsonRpcParseError, JsonRpcWireError):
            await self.app(
                scope,
                StatelessTransportMiddleware._replay_receive(body, receive),
                send,
            )
            return

        rejected = reject_modern_method_policy(
            request.method, request.id, self.protocol_era
        )
        if rejected is not None:
            await self._sender._send_pipeline_response(
                scope, send, raw_headers, rejected, body=body
            )
            return

        header_method = get_header(raw_headers, HEADER_MCP_METHOD)
        if header_method is not None:
            try:
                validate_header_body_method(header_method, request.method)
            except Exception as exc:
                from nitrostack.protocol.jsonrpc import map_exception_to_jsonrpc

                await self._sender._send_pipeline_response(
                    scope,
                    send,
                    raw_headers,
                    (400, map_exception_to_jsonrpc(exc, request.id)),
                    body=body,
                )
                return

        if request.method == "server/discover" and self.discover_handler is not None:
            from nitrostack.protocol.jsonrpc import jsonrpc_success

            await self._sender._send_pipeline_response(
                scope,
                send,
                raw_headers,
                (200, jsonrpc_success(request.id, self.discover_handler())),
                body=body,
            )
            return

        await self._sender._forward_with_stateless_headers(
            scope,
            StatelessTransportMiddleware._replay_receive(body, receive),
            send,
            body=body,
        )


def wrap_sessionless_http(
    app: ASGIApp,
    *,
    protocol_era: ProtocolEra = "auto",
    enable_cors: bool = True,
    discover_handler: Optional[DiscoverHandler] = None,
) -> ASGIApp:
    """Sessionless transport guard; official mcp 2.x still owns ``tools/call``."""
    return SessionlessHttpGuard(
        app,
        protocol_era=protocol_era,
        enable_cors=enable_cors,
        discover_handler=discover_handler,
    )


def wrap_modern_handshake_reject(app: ASGIApp, *, enable_cors: bool = True) -> ASGIApp:
    """Reject 2025 ``initialize`` on era ``modern``; leave the v2 app otherwise."""
    return wrap_sessionless_http(app, protocol_era="modern", enable_cors=enable_cors)


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
