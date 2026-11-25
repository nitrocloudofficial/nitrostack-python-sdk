"""HTTP app assembly: `build_http_app`."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, List, Optional

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from nitrostack.core.di import DIContainer
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import (
    HttpEngine,
    ProtocolEra,
    WireMode,
    protocol_version_for_era,
    resolve_http_engine,
)
from nitrostack.transports.cors import configured_cors_origins
from nitrostack.transports.headers import (
    CORS_EXPOSE_HEADER_NAMES,
    SSE_SUBSCRIPTIONS_PATH,
)
from nitrostack.transports.http.constants import (
    CORS_ALLOW_HEADERS,
    DEFAULT_ENDPOINT,
    logger,
)
from nitrostack.transports.http.env import _env_flag, _env_list
from nitrostack.transports.http.handlers import make_handlers
from nitrostack.transports.http.middleware import (
    ExactEndpointSlashMiddleware,
    HeaderCompatMiddleware,
    SessionCapMiddleware,
)
from nitrostack.transports.http.tracing import (
    RequestTraceMiddleware,
    _enable_trace_logging,
)
from nitrostack.transports.subscriptions import subscriptions_listen_endpoint

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication


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
        server.protocol_era = protocol_era

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
# Line 156
    mcp_asgi_app: ASGIApp = HeaderCompatMiddleware(
        handle_streamable_http,
        drop_session_headers=stateless,
        require_protocol_version=protocol_era == "modern",
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

    h = make_handlers(
        mcp_app,
        endpoint=endpoint,
        protocol_era=protocol_era,
        http_engine=http_engine,
        stateless=stateless,
        json_response=json_response,
        session_cap=session_cap,
    )

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
            Route("/.well-known/oauth-authorization-server", endpoint=h["oauth_not_supported"], methods=["GET", "POST"]),
            Route("/.well-known/oauth-protected-resource", endpoint=h["oauth_not_supported"], methods=["GET", "POST"]),
            Route("/register", endpoint=h["oauth_not_supported"], methods=["GET", "POST"]),
            Route("/oauth/v2/register", endpoint=h["oauth_not_supported"], methods=["GET", "POST"]),
        ]

    routes = [
        # More specific paths MUST come before the catch-all `Mount(endpoint, ...)`
        # below — Starlette matches routes in order, and a `Mount` matches any
        # path under its prefix, so `/mcp/health` would otherwise be swallowed
        # by the `/mcp` mount before ever reaching the health route.
        Route("/", endpoint=h["root_page"], methods=["GET"]),
        Route("/widgets/preview", endpoint=h["widgets_preview"], methods=["GET"]),
        Route("/widgets/preview/call", endpoint=h["widgets_preview_call"], methods=["POST"]),
        *oauth_stub_routes,
        Route("/json/version", endpoint=h["json_version"], methods=["GET"]),
        Route("/json/list", endpoint=h["json_list"], methods=["GET"]),
        Route("/json", endpoint=h["json_list"], methods=["GET"]),
        Route("/favicon.ico", endpoint=h["favicon"], methods=["GET"]),
        Route(f"{endpoint}/health", endpoint=h["health_check"], methods=["GET"]),
        Mount(sse_messages_path, app=sse_transport.handle_post_message),
        # `handle_streamable_http`/`sse_transport.handle_post_message` are raw
        # ASGI apps (scope, receive, send), so they must be `Mount`ed rather
        # than used as `Route` endpoints (which expect `Request -> Response`).
        Mount(endpoint, app=mcp_asgi_app),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
    ]
    if protocol_era != "legacy":
        routes.append(
            Route(
                SSE_SUBSCRIPTIONS_PATH,
                endpoint=subscriptions_listen_endpoint(mcp_app),
                methods=["GET", "POST"],
            )
        )

    # Rewrite `/mcp` → `/mcp/` *before* routing so Inspector never sees a 307.
    # CORS stays outermost so preflight still works on the original path.
    middleware = [
        Middleware(ExactEndpointSlashMiddleware, endpoint=endpoint),
    ]
    if enable_cors:
        expose_headers = list(CORS_EXPOSE_HEADER_NAMES)
        if http_engine == "sessionful":
            expose_headers.append(LEGACY_SESSION_HEADER)
        # Single CORS layer for `/mcp`: the sidecar that used to add a second
        # one has been removed (#22), so `MCP_CORS_ALLOWED_ORIGINS` is honored
        # here and nowhere else.
        allow_origins = list(configured_cors_origins()) or ["*"]
        middleware.insert(
            0,
            Middleware(
                CORSMiddleware,
                allow_origins=allow_origins,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                # SEP-2243 permits application-defined Mcp-Param-* names.
                # Starlette mirrors requested names when '*' is enabled.
                allow_headers=[*CORS_ALLOW_HEADERS, "*"],
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
    app.state.subscription_bus = getattr(mcp_app.mcp_server, "subscription_bus", None)
    app.state.protocol_version = protocol_version_for_era(protocol_era)
    return app