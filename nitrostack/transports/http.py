"""
Streamable HTTP transport for NitroStack (Phase 3).

The official `mcp` SDK's `StreamableHTTPSessionManager` already provides
per-session isolation (one transport + one `Server.run()` task per session),
SSE streaming, resumability via an event store, idle-session timeouts, and
DNS-rebinding protection via `TransportSecuritySettings`. This module does
NOT reimplement any of that — it wires those existing features through
NitroStack's config/env vars and adds the handful of pieces the manager does
not provide out of the box:

- CORS headers for browser-based MCP clients
- A concurrent-session cap with a `429` response (the manager has no public
  session-count API or creation hook, so this is tracked via a thin ASGI
  middleware watching the `mcp-session-id` header)
- A `/mcp/health` endpoint
- A root documentation page at `GET /` (browsers opening the HTTP port)
- Chrome DevTools discovery stubs at `GET /json` and `GET /json/version` so
  inspector probes do not 404
- JSON 404s for OAuth discovery / DCR (`/register`) so MCP Inspector does not
  treat the HTML landing page as an OAuth error
- Legacy SSE (`/sse` + `/mcp/messages/`) for older HTTP+SSE-only clients

See `dev-plan/PHASE-3-http-transport.md` for the full scope.
"""
from __future__ import annotations

import contextlib
import html
import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from nitrostack.widgets.preview_page import render_preview_page
from nitrostack.core.di import DIContainer
from pydantic_core import PydanticUndefined
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from nitrostack.protocol.version import (
    HttpEngine,
    ProtocolEra,
    WireMode,
    protocol_version_for_era,
    resolve_http_engine,
)
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.transports.headers import (
    CORS_ALLOW_HEADER_NAMES,
    CORS_EXPOSE_HEADER_NAMES,
    strip_legacy_session_headers_asgi,
)
from nitrostack.transports.proxy import public_url_for_request

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication

logger = logging.getLogger("nitrostack.transports.http")

DEFAULT_ENDPOINT = "/mcp"

# Shared 2026 allow-headers. Session id is exposed only on the sessionful engine.
CORS_ALLOW_HEADERS = list(CORS_ALLOW_HEADER_NAMES)

# The Accept value `StreamableHTTPServerTransport` requires: it needs
# `application/json` on POST and `text/event-stream` on both POST and GET.
MCP_ACCEPT = "application/json, text/event-stream"

_PROCESS_START = time.monotonic()


def _server_meta(mcp_app: "McpApplication") -> Dict[str, str]:
    cfg = getattr(mcp_app, "server_config", None)
    return {
        "name": getattr(cfg, "name", None) or "NitroStack MCP Server",
        "version": getattr(cfg, "version", None) or "1.0.0",
    }


def _landing_html(
    name: str,
    version: str,
    endpoint: str,
    public_mcp_url: Optional[str] = None,
) -> str:
    safe_name = html.escape(name)
    safe_version = html.escape(version)
    mcp_path = html.escape(public_mcp_url or (endpoint.rstrip("/") or "/mcp"))
    health_path = html.escape(f"{(endpoint.rstrip('/') or '/mcp')}/health")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_name}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      background: radial-gradient(circle at 10% 20%, #0f172a 0%, #020617 90%);
      color: #f8fafc;
    }}
    main {{
      width: min(640px, calc(100% - 2rem));
      background: rgba(30, 41, 59, 0.55);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px; padding: 2rem;
    }}
    h1 {{ margin: 0 0 0.35rem; font-size: 1.6rem; }}
    p {{ color: #94a3b8; margin: 0 0 1.25rem; }}
    code, a {{ color: #a5b4fc; }}
    ul {{ margin: 0; padding-left: 1.2rem; line-height: 1.8; }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_name}</h1>
    <p>NitroStack MCP server v{safe_version}. This is not a website — connect with an MCP client.</p>
    <ul>
      <li>Streamable HTTP: <code>POST {mcp_path}</code></li>
      <li>Legacy SSE: <code>GET /sse</code></li>
      <li>Widget preview: <a href="/widgets/preview"><code>/widgets/preview</code></a></li>
      <li>Health: <a href="{health_path}"><code>{health_path}</code></a></li>
    </ul>
  </main>
</body>
</html>
"""


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _enable_trace_logging() -> None:
    """
    Give `logger` its own stderr handler so tracing works regardless of how the
    host application configured logging.

    `FileLogger` only attaches handlers to the `nitrostack` logger when it is
    first instantiated, which for an HTTP server may not happen until a tool
    runs. Until then this module's records fall through to `logging`'s
    last-resort handler and INFO is discarded. Propagation is disabled so the
    traces are not emitted twice once `FileLogger` does attach its handler.
    stderr keeps them off stdout, which dual mode uses for JSON-RPC.
    """
    if any(getattr(handler, "_nitrostack_trace", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] (%(name)s): %(message)s"))
    handler._nitrostack_trace = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class RequestTraceMiddleware:
    """
    Log the raw request line, headers and body of every HTTP request, plus the
    response status and headers.

    Enabled only when `NITROSTACK_HTTP_DEBUG=1`. uvicorn's access log has no
    timestamps and no headers, which makes it impossible to tell *why* the
    `mcp` SDK rejected a client request (a `406` and a `400` from the SDK are
    both header-driven) or even to correlate a failed client connection with a
    log line. This middleware is the outermost layer so it sees requests
    exactly as the client sent them, before CORS or any path rewriting.
    """

    MAX_BODY_CHARS = 2000
    REDACTED_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @classmethod
    def _decode_headers(cls, raw: Any) -> Dict[str, str]:
        headers = {}
        for key, value in raw or []:
            name = key.decode("latin-1")
            headers[name] = "<redacted>" if name.lower() in cls.REDACTED_HEADERS else value.decode("latin-1")
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method")
        path = scope.get("path")
        query = (scope.get("query_string") or b"").decode("latin-1")
        target = f"{path}?{query}" if query else path
        logger.info("trace >> %s %s headers=%s", method, target, self._decode_headers(scope.get("headers")))

        async def traced_receive() -> Any:
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                if body:
                    text = body.decode("utf-8", errors="replace")
                    if len(text) > self.MAX_BODY_CHARS:
                        text = text[: self.MAX_BODY_CHARS] + "...<truncated>"
                    logger.info("trace >> %s %s body=%s", method, target, text)
            return message

        async def traced_send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                logger.info(
                    "trace << %s %s status=%s headers=%s",
                    method,
                    target,
                    message.get("status"),
                    self._decode_headers(message.get("headers")),
                )
            await send(message)

        await self.app(scope, traced_receive, traced_send)


def _preview_default_arguments(entry: Any) -> Dict[str, Any]:
    """Prefill live preview with tool examples (shopId, openNow, …)."""
    examples = getattr(getattr(entry, "config", None), "examples", None)
    raw = getattr(examples, "input", None) if examples is not None else None
    if isinstance(raw, dict):
        return dict(raw)
    model = getattr(entry, "input_model", None)
    if model is None:
        return {}
    data: Dict[str, Any] = {}
    for fname, field in getattr(model, "model_fields", {}).items():
        default = getattr(field, "default", PydanticUndefined)
        if default is not PydanticUndefined and default is not None:
            data[fname] = default
            continue
        if fname in ("shopId", "shop_id"):
            data[fname] = "tonys-pizza"
        elif fname == "product_id":
            data[fname] = "sku-1"
        elif fname == "filter":
            data[fname] = "all"
        elif fname == "openNow":
            data[fname] = True
        elif fname == "origin":
            data[fname] = "JFK"
        elif fname == "destination":
            data[fname] = "LAX"
        elif fname in ("departureDate", "departure_date"):
            data[fname] = "2026-09-15"
        elif fname in ("offerId", "offer_id"):
            data[fname] = "off_mock123456"
        elif fname in ("orderId", "order_id"):
            data[fname] = "ord_mock123456"
        elif fname == "query":
            data[fname] = "London"
    return data


class ExactEndpointSlashMiddleware:
    """
    Internally rewrite `/mcp` → `/mcp/` so Starlette does not 307.

    `Mount("/mcp")` only matches `/mcp/...`. With `redirect_slashes=True`
    (Starlette's default) a request to `/mcp` becomes `307 Location: /mcp/`.
    MCP Inspector's Streamable HTTP client POSTs and GETs `/mcp` with no
    trailing slash; following that 307 on the GET SSE stream drops the
    connection, so List Tools shows a blank page and disconnects.
    """

    def __init__(self, app: ASGIApp, endpoint: str) -> None:
        self.app = app
        self._exact = endpoint.rstrip("/") or "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == self._exact:
            scope = dict(scope)
            scope["path"] = self._exact + "/"
            raw = scope.get("raw_path")
            if isinstance(raw, (bytes, bytearray)):
                scope["raw_path"] = raw.rstrip(b"/") + b"/"
        await self.app(scope, receive, send)


class HeaderCompatMiddleware:
    """
    Normalize ``Accept`` on the Streamable HTTP mount so wildcard or absent
    Accept does not 406. Session ids are stripped only when
    ``drop_session_headers`` is set.

    ``MCP-Protocol-Version`` is never deleted. Duplicate casings are collapsed
    to one ``mcp-protocol-version`` entry and the client value is kept so later
    version checks see what the client sent.

    Stack order on the HTTP app: CORS → this middleware (preserve) → handler.
    Sidecar version checks run on the combined app outside this mount.
    """

    def __init__(self, app: ASGIApp, *, drop_session_headers: bool = False) -> None:
        self.app = app
        self.drop_session_headers = drop_session_headers

    @staticmethod
    def _normalize_accept(value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return MCP_ACCEPT
        media_types = [media_type.strip() for media_type in value.split(",")]
        has_json = any(media_type.startswith("application/json") for media_type in media_types)
        has_sse = any(media_type.startswith("text/event-stream") for media_type in media_types)
        if has_json and has_sse:
            return None
        # Only a wildcard is rewritten. A client that names concrete media types
        # but omits one the transport needs is genuinely non-compliant (the spec
        # requires both on POST), so it keeps getting the transport's 406.
        if any(media_type.startswith("*/*") for media_type in media_types):
            return MCP_ACCEPT
        return None

    @staticmethod
    def _canonicalize_protocol_version(headers: List[Any]) -> List[Any]:
        """Keep the protocol version value; emit one lowercase header name."""
        version_values: List[bytes] = []
        kept: List[Any] = []
        for key, value in headers:
            if key.lower() == b"mcp-protocol-version":
                version_values.append(value)
            else:
                kept.append((key, value))
        if not version_values:
            return headers
        kept.append((b"mcp-protocol-version", version_values[0]))
        return kept

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: List[Any] = list(scope.get("headers") or [])
        if self.drop_session_headers:
            headers = strip_legacy_session_headers_asgi(headers)
        headers = self._canonicalize_protocol_version(headers)

        raw_accept = next((value for key, value in headers if key.lower() == b"accept"), None)
        accept = self._normalize_accept(raw_accept.decode("latin-1") if raw_accept is not None else None)
        if accept is not None:
            headers = [(key, value) for key, value in headers if key.lower() != b"accept"]
            headers.append((b"accept", accept.encode("latin-1")))
            logger.debug("Rewrote Accept %r -> %r for %s", raw_accept, accept, scope.get("path"))

        scope = dict(scope)
        scope["headers"] = headers
        await self.app(scope, receive, send)


class SessionCapMiddleware:
    """
    Enforce a `max_sessions` concurrent-session cap on the Streamable HTTP
    mount, returning a `429` JSON-RPC error once at capacity.

    `StreamableHTTPSessionManager` doesn't expose a public session count or a
    session-created/closed hook, so this middleware tracks session ids itself
    by observing the `mcp-session-id` request header (existing sessions) and
    the `Mcp-Session-Id` response header (newly created sessions), and prunes
    entries locally after `session_idle_timeout` so the count self-heals even
    if a client disconnects without sending `DELETE`.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_sessions: Optional[int],
        session_idle_timeout: Optional[float] = None,
    ) -> None:
        self.app = app
        self.max_sessions = max_sessions
        self.session_idle_timeout = session_idle_timeout
        self._sessions: Dict[str, float] = {}

    def _prune_stale(self) -> None:
        if not self.session_idle_timeout:
            return
        cutoff = time.monotonic() - self.session_idle_timeout
        for sid in [s for s, seen in self._sessions.items() if seen < cutoff]:
            self._sessions.pop(sid, None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.max_sessions:
            await self.app(scope, receive, send)
            return

        self._prune_stale()

        headers = dict(scope.get("headers") or [])
        raw_session_id = headers.get(b"mcp-session-id")
        session_id = raw_session_id.decode() if raw_session_id else None
        is_known = session_id is not None and session_id in self._sessions

        if not is_known and len(self._sessions) >= self.max_sessions:
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "Too many sessions: server at capacity, retry later",
                    },
                    "id": None,
                },
                status_code=429,
            )
            await response(scope, receive, send)
            return

        if is_known:
            self._sessions[session_id] = time.monotonic()

        new_session_id: Dict[str, Optional[str]] = {"value": None}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                for key, value in message.get("headers", []):
                    if key.lower() == b"mcp-session-id":
                        new_session_id["value"] = value.decode()
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if new_session_id["value"]:
            self._sessions[new_session_id["value"]] = time.monotonic()

        if scope.get("method") == "DELETE" and session_id:
            self._sessions.pop(session_id, None)

    @property
    def active_session_count(self) -> int:
        self._prune_stale()
        return len(self._sessions)


def _oauth_is_configured() -> bool:
    """True when ``OAuthModule.for_root`` registered an ``OAuthService`` instance."""
    try:
        from nitrostack.auth.oauth import OAuthService

        return DIContainer.get_instance().has_value(OAuthService)
    except Exception:
        return False


def build_http_app(
    mcp_app: "McpApplication",
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    max_sessions: Optional[int] = None,
    session_idle_timeout: Optional[float] = None,
    enable_cors: bool = True,
    stateless: bool = False,
    json_response: bool = False,
    protocol_era: ProtocolEra = "auto",
    wire_mode: WireMode = "stateless",
    http_engine: Optional[HttpEngine] = None,
) -> Starlette:
    """
    Build the Starlette app exposing NitroStack's owned low-level server over
    Streamable HTTP (`/mcp`), legacy SSE (`/sse` + `/mcp/messages/`), and a
    health check (`/mcp/health`).

    Args:
        mcp_app: The bootstrapped `McpApplication` whose `mcp_server` (and
            registered tools/resources/prompts) this app serves.
        endpoint: Streamable HTTP mount path (default `/mcp`).
        max_sessions: Optional concurrent-session cap; `None`/`0` disables the cap.
        session_idle_timeout: Optional idle timeout in seconds for stateful
            sessions (ignored when `stateless=True`).
        enable_cors: Whether to add permissive CORS headers (default `True`).
            When `False`, DNS-rebinding
            protection (Origin/Host validation) is enabled instead, configured
            via `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` env vars.
        stateless: When `True`, every request gets a fresh transport with no
            session id and no session tracking (see `StreamableHTTPSessionManager`).
            This is the primitive the `2026-07-28` stateless MCP spec needs;
            exposed here as a config knob so adopting that spec later doesn't
            require touching this wiring again.
        json_response: When `True`, a POST carrying a JSON-RPC request is
            answered with a plain `application/json` body instead of a
            `text/event-stream` SSE frame. The spec allows either, but a client
            that doesn't implement SSE parsing for POST responses sees the SSE
            form as a stream that ended without a result. Server-initiated
            streaming (progress, notifications) is unavailable in this mode.
        protocol_era: ``legacy`` / ``modern`` / ``auto``. Distinct from ``stateless``.
        wire_mode: Dual-spec policy for 2025 traffic (``sessionful``, ``stateless``
            fallback, or ``reject``). ``auto`` uses ``stateless``; ``modern`` uses
            ``reject``.
        http_engine: Era factory result. ``sessionless`` for modern/auto,
            ``sessionful`` only for legacy. ``auto`` / ``modern`` never start
            a sessionful manager, even if ``stateless=False``.
    """
    http_engine = resolve_http_engine(
        protocol_era, http_engine=http_engine, stateless=stateless
    )
    stateless = http_engine == "sessionless"

    server = mcp_app.mcp_server
    if server is not None:
        server.http_engine = http_engine
        server.sessionful = http_engine == "sessionful"

    security_settings = None
    if not enable_cors:
        allowed_hosts = _env_list("MCP_ALLOWED_HOSTS") or ["localhost:*", "127.0.0.1:*"]
        allowed_origins = _env_list("MCP_ALLOWED_ORIGINS")
        security_settings = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    else:
        security_settings = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    # Official mcp 2.x owns /mcp (streamable HTTP, both protocol eras).
    # One manager only; auto/modern are sessionless and legacy is sessionful.
    session_manager = StreamableHTTPSessionManager(
        app=mcp_app.mcp_server,
        stateless=stateless,
        json_response=json_response,
        session_idle_timeout=None if stateless else session_idle_timeout,
        security_settings=security_settings,
    )
    # Trailing slash is required: Starlette's Mount("/mcp/messages") only matches
    # "/mcp/messages/" (or deeper), NOT bare "/mcp/messages". Without the slash the
    # endpoint event points at a path that falls through to Mount("/mcp") (Streamable
    # HTTP), which then 406s SSE clients that don't send Streamable Accept headers.
    # Matches the mcp SDK's own SseServerTransport example ("/messages/").
    sse_messages_path = f"{endpoint.rstrip('/')}/messages/"
    sse_transport = SseServerTransport(sse_messages_path)

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    # Wraps only the Streamable HTTP mount, so `/mcp/health` and the legacy SSE
    # routes keep their own (correct) content negotiation.
    mcp_asgi_app: ASGIApp = HeaderCompatMiddleware(
        handle_streamable_http, drop_session_headers=stateless
    )
    session_cap: Optional[SessionCapMiddleware] = None
    if max_sessions and not stateless:
        session_cap = SessionCapMiddleware(
            mcp_asgi_app, max_sessions=max_sessions, session_idle_timeout=session_idle_timeout
        )
        mcp_asgi_app = session_cap

    async def handle_sse(request):
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp_app.mcp_server.run(streams[0], streams[1], mcp_app.mcp_server.create_initialization_options())
        return Response()

    def _request_public_mcp_url(request) -> str:
        port = os.environ.get("PORT") or os.environ.get("MCP_SERVER_PORT") or "3000"
        return public_url_for_request(
            request,
            path=endpoint.rstrip("/") or "/mcp",
            fallback_host=f"localhost:{port}",
        )

    async def health_check(request):
        return JSONResponse(
            {
                "status": "ok",
                "transport": "streamable-http",
                "protocolVersion": protocol_version_for_era(protocol_era),
                "protocolEra": protocol_era,
                "statelessCapable": http_engine == "sessionless",
                "stateless": stateless,
                "jsonResponse": json_response,
                "sessions": session_cap.active_session_count if session_cap else None,
                "uptimeSeconds": round(time.monotonic() - _PROCESS_START, 2),
                "publicUrl": _request_public_mcp_url(request),
            }
        )

    async def root_page(request):
        meta = _server_meta(mcp_app)
        return HTMLResponse(
            _landing_html(
                meta["name"],
                meta["version"],
                endpoint,
                public_mcp_url=_request_public_mcp_url(request),
            )
        )

    async def oauth_not_supported(request):
        """Inspector DCR posts `/register` when Authentication is on.

        Return JSON (not the HTML 404 page) so the client shows a clear
        OAuth-off message instead of `Unexpected token '<'`.
        """
        connect_url = _request_public_mcp_url(request)
        return JSONResponse(
            {
                "error": "invalid_request",
                "error_description": (
                    "This MCP server does not use OAuth. In MCP Inspector turn "
                    "Authentication off, then connect with Streamable HTTP to "
                    f"{connect_url}."
                ),
            },
            status_code=404,
        )

    async def widgets_preview(request):
        catalog = []
        for name, entry in getattr(mcp_app, "_tools", {}).items():
            component = getattr(entry, "component", None)
            if component is None:
                continue
            catalog.append(
                {
                    "name": name,
                    "resourceUri": component.resource_uri,
                    "arguments": _preview_default_arguments(entry),
                }
            )
        return HTMLResponse(render_preview_page(catalog))

    async def widgets_preview_call(request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"}, status_code=400)
        name = body.get("tool") or ""
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            return JSONResponse({"error": "arguments must be an object"}, status_code=400)
        entry = getattr(mcp_app, "_tools", {}).get(name)
        if entry is None or getattr(entry, "component", None) is None:
            return JSONResponse({"error": f"No widget tool named {name!r}"}, status_code=404)
        try:
            result = await mcp_app._call_tool(name, arguments)
        except Exception as exc:
            logger.exception("Widget preview tool call failed for %s", name)
            return JSONResponse({"error": str(exc)}, status_code=400)
        structured = getattr(result, "structured_content", None)
        if structured is None:
            structured = getattr(result, "structuredContent", None)
        try:
            html = entry.component.html_with_data(structured)
        except Exception as exc:
            logger.exception("Widget preview render failed for %s", name)
            return JSONResponse(
                {
                    "error": f"Widget render failed: {exc}",
                    "structuredContent": structured,
                    "html": None,
                    "resourceUri": entry.component.resource_uri,
                    "isError": True,
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "structuredContent": structured,
                "html": html,
                "resourceUri": entry.component.resource_uri,
                "isError": bool(getattr(result, "is_error", None) if getattr(result, "is_error", None) is not None else getattr(result, "isError", False)),
            }
        )

    async def json_version(request):
        """Chrome/Cursor DevTools probe `/json/version` when a tab opens localhost."""
        meta = _server_meta(mcp_app)
        health_path = f"{endpoint.rstrip('/')}/health"
        return JSONResponse(
            {
                "Browser": meta["name"],
                "Protocol-Version": "2025-06-18",
                "User-Agent": f"NitroStack/{meta['version']}",
                "webSocketDebuggerUrl": "",
                "transport": "mcp",
                "publicUrl": _request_public_mcp_url(request),
                "endpoints": {
                    "mcp": endpoint,
                    "sse": "/sse",
                    "health": health_path,
                },
            }
        )

    async def json_list(request):
        """Chrome DevTools `/json` and `/json/list` expect a page list; empty = not CDP."""
        return JSONResponse([])

    async def favicon(request):
        return Response(status_code=204)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            logger.info(
                "StreamableHTTP session manager started (stateless=%s, max_sessions=%s, "
                "session_idle_timeout=%s)",
                stateless,
                max_sessions,
                session_idle_timeout,
            )
            yield

    oauth_stub_routes: List[Route] = []
    if not _oauth_is_configured():
        # Inspector DCR / discovery stubs. Omit when OAuthModule is registered so
        # a real protected-resource document is not replaced with "no OAuth".
        oauth_stub_routes = [
            Route("/.well-known/oauth-authorization-server", endpoint=oauth_not_supported, methods=["GET", "POST"]),
            Route("/.well-known/oauth-protected-resource", endpoint=oauth_not_supported, methods=["GET", "POST"]),
            Route("/register", endpoint=oauth_not_supported, methods=["GET", "POST"]),
            Route("/oauth/v2/register", endpoint=oauth_not_supported, methods=["GET", "POST"]),
        ]

    routes = [
        # More specific paths MUST come before the catch-all `Mount(endpoint, ...)`
        # below — Starlette matches routes in order, and a `Mount` matches any
        # path under its prefix, so `/mcp/health` would otherwise be swallowed
        # by the `/mcp` mount before ever reaching the health route.
        Route("/", endpoint=root_page, methods=["GET"]),
        Route("/widgets/preview", endpoint=widgets_preview, methods=["GET"]),
        Route("/widgets/preview/call", endpoint=widgets_preview_call, methods=["POST"]),
        *oauth_stub_routes,
        Route("/json/version", endpoint=json_version, methods=["GET"]),
        Route("/json/list", endpoint=json_list, methods=["GET"]),
        Route("/json", endpoint=json_list, methods=["GET"]),
        Route("/favicon.ico", endpoint=favicon, methods=["GET"]),
        Route(f"{endpoint}/health", endpoint=health_check, methods=["GET"]),
        Mount(sse_messages_path, app=sse_transport.handle_post_message),
        # `handle_streamable_http`/`sse_transport.handle_post_message` are raw
        # ASGI apps (scope, receive, send), so they must be `Mount`ed rather
        # than used as `Route` endpoints (which expect `Request -> Response`).
        Mount(endpoint, app=mcp_asgi_app),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
    ]

    # Rewrite `/mcp` → `/mcp/` *before* routing so Inspector never sees a 307.
    # CORS stays outermost so preflight still works on the original path.
    middleware = [
        Middleware(ExactEndpointSlashMiddleware, endpoint=endpoint),
    ]
    if enable_cors:
        expose_headers = list(CORS_EXPOSE_HEADER_NAMES)
        if http_engine == "sessionful":
            expose_headers.append(LEGACY_SESSION_HEADER)
        middleware.insert(
            0,
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=CORS_ALLOW_HEADERS,
                expose_headers=expose_headers,
            ),
        )

    if _env_flag("NITROSTACK_HTTP_DEBUG"):
        _enable_trace_logging()
        middleware.insert(0, Middleware(RequestTraceMiddleware))

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.protocol_era = protocol_era
    app.state.wire_mode = wire_mode
    app.state.stateless = stateless
    app.state.http_engine = http_engine
    app.state.sessionful = http_engine == "sessionful"
    app.state.session_manager = session_manager
    app.state.streamable_http_manager_count = 1
    return app
