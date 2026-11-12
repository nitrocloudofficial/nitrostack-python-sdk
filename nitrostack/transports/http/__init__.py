"""
Streamable HTTP transport for NitroStack (Phase 3).

The official `mcp` SDK's `StreamableHTTPSessionManager` already provides
per-session isolation (one transport + one `Server.run()` task per session),
SSE streaming, resumability via an event store, idle-session timeouts, and
DNS-rebinding protection via `TransportSecuritySettings`. This package does
NOT reimplement any of that — it wires those existing features through
NitroStack's config/env vars and adds the handful of pieces the manager does
not provide out of the box:

- CORS headers for browser-based MCP clients
- A concurrent-session cap with a `429` response (the manager has no public
  session-count API or creation hook, so this is tracked via a thin ASGI
  middleware watching the `mcp-session-id` header)
- A `/mcp/health` endpoint
- A documentation landing page at `GET /` (connection setup + tool catalog)
- Chrome DevTools discovery stubs at `GET /json` and `GET /json/version` so
  inspector probes do not 404
- JSON 404s for OAuth discovery / DCR (`/register`) so MCP Inspector does not
  treat the HTML landing page as an OAuth error
- Legacy SSE (`/sse` + `/mcp/messages/`) for older HTTP+SSE-only clients

Module layout (dependencies point one way, top to bottom):
    constants, env  ->  middleware, tracing, handlers  ->  app  ->  __init__

This package replaces the former single module `nitrostack/transports/http.py`.
Names are re-exported here so existing imports keep working.

See `dev-plan/PHASE-3-http-transport.md` for the full scope.
"""
from nitrostack.transports.http.app import _oauth_is_configured, build_http_app
from nitrostack.transports.http.constants import (
    CORS_ALLOW_HEADERS,
    DEFAULT_ENDPOINT,
    MCP_ACCEPT,
    _PROCESS_START,
    logger,
)
from nitrostack.transports.http.env import _env_flag, _env_list
from nitrostack.transports.http.handlers import (
    _landing_tools,
    _preview_default_arguments,
    _server_meta,
)
from nitrostack.transports.http.middleware import (
    ExactEndpointSlashMiddleware,
    HeaderCompatMiddleware,
    SessionCapMiddleware,
)
from nitrostack.transports.http.tracing import (
    RequestTraceMiddleware,
    _enable_trace_logging,
)

__all__ = [
    "build_http_app",
    "DEFAULT_ENDPOINT",
    "CORS_ALLOW_HEADERS",
    "MCP_ACCEPT",
    "ExactEndpointSlashMiddleware",
    "HeaderCompatMiddleware",
    "SessionCapMiddleware",
    "RequestTraceMiddleware",
]