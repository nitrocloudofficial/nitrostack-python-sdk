"""CORS configuration for stateless MCP HTTP."""

from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.transports.headers import (
    CORS_ALLOW_HEADER_NAMES,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_EXPOSE_HEADER_NAMES,
    CORS_EXPOSE_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_METHODS,
    HEADER_ACCESS_CONTROL_ALLOW_ORIGIN,
    HEADER_ACCESS_CONTROL_EXPOSE_HEADERS,
    HEADER_MCP_PARAM_PREFIX,
    MCP_HTTP_PATH,
    SSE_SUBSCRIPTIONS_PATH,
    get_header,
)


def configured_cors_origins() -> tuple[str, ...]:
    """Comma-separated allowlist from ``MCP_CORS_ALLOWED_ORIGINS``."""
    raw = os.environ.get("MCP_CORS_ALLOWED_ORIGINS", "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def resolve_allowed_origin(
    origin: Optional[str] = None,
    *,
    allow_origin: str = "*",
    allowed_origins: Optional[Sequence[str]] = None,
) -> str:
    """
    Choose ``Access-Control-Allow-Origin`` without reflecting arbitrary Origins.

    An explicit allowlist (argument or ``MCP_CORS_ALLOWED_ORIGINS``) is required
    before a request Origin is echoed. Otherwise the configured ``allow_origin``
    default (``*``) is used.
    """
    allowlist = (
        tuple(allowed_origins) if allowed_origins is not None else configured_cors_origins()
    )
    if allowlist:
        if origin and origin in allowlist:
            return origin
        if allow_origin != "*" and allow_origin in allowlist:
            return allow_origin
        return allowlist[0]
    return allow_origin


def build_cors_headers(
    origin: Optional[str] = None,
    *,
    allow_origin: str = "*",
    allowed_origins: Optional[Sequence[str]] = None,
) -> dict[str, str]:
    """Build CORS headers for MCP browser clients (SEP-2243 & SEP-2575)."""
    resolved_origin = resolve_allowed_origin(
        origin,
        allow_origin=allow_origin,
        allowed_origins=allowed_origins,
    )
    return {
        HEADER_ACCESS_CONTROL_ALLOW_ORIGIN: resolved_origin,
        HEADER_ACCESS_CONTROL_ALLOW_METHODS: CORS_ALLOW_METHODS,
        HEADER_ACCESS_CONTROL_ALLOW_HEADERS: CORS_ALLOW_HEADERS,
        HEADER_ACCESS_CONTROL_EXPOSE_HEADERS: CORS_EXPOSE_HEADERS,
    }


def requested_mcp_param_headers(request_headers: Mapping[str, str]) -> tuple[str, ...]:
    """Exact ``Mcp-Param-*`` names from ``Access-Control-Request-Headers``."""
    raw = get_header(request_headers, "Access-Control-Request-Headers") or ""
    prefix = HEADER_MCP_PARAM_PREFIX.lower()
    echoed: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if name.lower().startswith(prefix) and len(name) > len(HEADER_MCP_PARAM_PREFIX):
            echoed.append(name)
    return tuple(echoed)


def cors_preflight_response_headers(request_headers: Mapping[str, str]) -> dict[str, str]:
    """Headers for OPTIONS preflight — HTTP 204 No Content."""
    origin = get_header(request_headers, "Origin")
    headers = build_cors_headers(origin=origin)
    extra = requested_mcp_param_headers(request_headers)
    if extra:
        current = headers[HEADER_ACCESS_CONTROL_ALLOW_HEADERS]
        headers[HEADER_ACCESS_CONTROL_ALLOW_HEADERS] = current + ", " + ", ".join(extra)
    return headers


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------

#: Paths the Streamable HTTP endpoint answers on (with and without the slash).
_MCP_CORS_PATHS = frozenset({MCP_HTTP_PATH, f"{MCP_HTTP_PATH}/", SSE_SUBSCRIPTIONS_PATH})


class McpCorsMiddleware:
    """
    CORS for the MCP endpoint, using the policy in this module.

    This is **only** CORS. It never reads a JSON-RPC body or answers a protocol
    request: the official MCP SDK owns ``/mcp``.

    On the MCP paths it

    * answers ``OPTIONS`` with ``204 No Content`` and
      :func:`cors_preflight_response_headers` (so ``Mcp-Param-*`` request headers
      are echoed), and
    * adds :func:`build_cors_headers` to every other response, so
      ``MCP_CORS_ALLOWED_ORIGINS`` is honoured and the ``Mcp-*`` headers are exposed.

    ``Mcp-Session-Id`` is exposed only when ``expose_session_id`` is true (the
    sessionful ``legacy`` engine); sessionless eras never carry it.

    Every other route (health, landing page, OAuth) keeps the permissive
    Starlette ``CORSMiddleware`` behaviour it always had.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        expose_session_id: bool = False,
        fallback_allow_headers: Optional[Sequence[str]] = None,
    ) -> None:
        self.app = app
        self.expose_session_id = expose_session_id
        expose = list(CORS_EXPOSE_HEADER_NAMES)
        if expose_session_id:
            expose.append(LEGACY_SESSION_HEADER)
        self._expose_value = ", ".join(expose)
        self._other_routes: ASGIApp = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=list(fallback_allow_headers or CORS_ALLOW_HEADER_NAMES),
            expose_headers=expose,
        )

    def _headers(self, origin: Optional[str]) -> dict[str, str]:
        headers = build_cors_headers(origin=origin)
        headers[HEADER_ACCESS_CONTROL_EXPOSE_HEADERS] = self._expose_value
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _MCP_CORS_PATHS:
            await self._other_routes(scope, receive, send)
            return

        request_headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in (scope.get("headers") or [])
        }

        if scope.get("method", "GET").upper() == "OPTIONS":
            headers = cors_preflight_response_headers(request_headers)
            headers[HEADER_ACCESS_CONTROL_EXPOSE_HEADERS] = self._expose_value
            if configured_cors_origins():
                headers["Vary"] = "Origin"
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (k.lower().encode("latin-1"), v.encode("latin-1"))
                        for k, v in headers.items()
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        cors = self._headers(get_header(request_headers, "Origin"))
        vary = bool(configured_cors_origins())

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name.lower() for name in cors}
                kept = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.decode("latin-1").lower() not in names
                ]
                kept.extend(
                    (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in cors.items()
                )
                if vary:
                    kept.append((b"vary", b"Origin"))
                message = {**message, "headers": kept}
            await send(message)

        await self.app(scope, receive, send_with_cors)
