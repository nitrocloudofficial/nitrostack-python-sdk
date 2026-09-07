"""Stateless HTTP ASGI middleware."""

from __future__ import annotations

from typing import Any, Callable, Optional

from nitrostack.runtime.stateless import assert_stateless_headers
from nitrostack.transports.cors import build_cors_headers, cors_preflight_response_headers
from nitrostack.transports.dispatch import IngressContext, StatelessIngressPipeline
from nitrostack.transports.headers import (
    MCP_HTTP_PATH,
    build_mcp_response_headers,
    get_header,
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
    ) -> None:
        self.app = app
        self.pipeline = pipeline
        self.mcp_paths = mcp_paths

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")

        if method == "OPTIONS" and path in self.mcp_paths:
            await self._send_options(scope, receive, send)
            return

        if method == "POST" and path in self.mcp_paths and self.pipeline is not None:
            body = await self._read_body(receive)
            handled = await self._try_pre_dispatch(scope, body, send)
            if handled:
                return
            receive = self._replay_receive(body, receive)

        await self._forward_with_stateless_headers(scope, receive, send)

    async def _send_options(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers_list = scope.get("headers") or []
        req_headers = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in headers_list
        }
        cors = cors_preflight_response_headers(req_headers)
        response_headers = build_mcp_response_headers(extra=cors)
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
        req_headers = strip_legacy_session_headers(
            {k.decode("latin-1"): v.decode("latin-1") for k, v in headers_list}
        )

        assert self.pipeline is not None
        result = await self.pipeline.handle_post(body, req_headers)
        if result is None:
            return False

        status, jsonrpc_response = result
        origin = get_header(req_headers, "Origin")
        cors = build_cors_headers(origin=origin)
        response_headers = build_mcp_response_headers(extra=cors)
        assert_stateless_headers(response_headers)

        payload = self.pipeline.serialize_response(jsonrpc_response)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": self._encode_headers(response_headers),
            }
        )
        await send({"type": "http.response.body", "body": payload})
        return True

    async def _forward_with_stateless_headers(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
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
                merged = build_mcp_response_headers(
                    content_type=raw_headers.get("content-type", "application/json"),
                    extra={
                        **build_cors_headers(origin=get_header(req_headers, "Origin")),
                        **strip_legacy_session_headers(raw_headers),
                    },
                )
                assert_stateless_headers(merged)
                message = {
                    **message,
                    "headers": self._encode_headers(merged),
                }
            await send(message)

        await self.app(scope, receive, send_wrapper)

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
        )
    )
    return StatelessTransportMiddleware(app, pipeline=pipeline)
