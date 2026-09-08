"""MCP 2.0 HTTP header names and builders."""

from __future__ import annotations

from typing import Mapping, Optional

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION

# Request headers
HEADER_CONTENT_TYPE = "Content-Type"
HEADER_MCP_PROTOCOL_VERSION = "MCP-Protocol-Version"
HEADER_MCP_METHOD = "Mcp-Method"
HEADER_MCP_NAME = "Mcp-Name"
HEADER_MCP_PARAM_PREFIX = "Mcp-Param-"
HEADER_AUTHORIZATION = "Authorization"
HEADER_LAST_EVENT_ID = "Last-Event-ID"

# Response headers
HEADER_VARY = "Vary"

# CORS headers
HEADER_ACCESS_CONTROL_ALLOW_ORIGIN = "Access-Control-Allow-Origin"
HEADER_ACCESS_CONTROL_ALLOW_METHODS = "Access-Control-Allow-Methods"
HEADER_ACCESS_CONTROL_ALLOW_HEADERS = "Access-Control-Allow-Headers"
HEADER_ACCESS_CONTROL_EXPOSE_HEADERS = "Access-Control-Expose-Headers"

# SSE anti-buffering
HEADER_X_ACCEL_BUFFERING = "X-Accel-Buffering"
HEADER_CACHE_CONTROL = "Cache-Control"

MCP_JSON_CONTENT_TYPE = "application/json"
MCP_SSE_CONTENT_TYPE = "text/event-stream"

CORS_ALLOW_METHODS = "GET, POST, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = (
    "Content-Type, Authorization, MCP-Protocol-Version, Mcp-Method, Mcp-Name, "
    "Mcp-Param-*, Last-Event-ID"
)
CORS_EXPOSE_HEADERS = "MCP-Protocol-Version, Mcp-Method, Mcp-Name"

MCP_HTTP_PATH = "/mcp"
SSE_SUBSCRIPTIONS_PATH = "/subscriptions/listen"


def normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Case-insensitive header map keyed by original casing where possible."""
    return {k: v for k, v in headers.items()}


def get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Fetch a header value case-insensitively."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def strip_legacy_session_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove legacy Mcp-Session-Id from incoming or outgoing headers."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() != LEGACY_SESSION_HEADER.lower()
    }


def strip_legacy_session_headers_asgi(
    headers: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    """Remove ``Mcp-Session-Id`` from an ASGI header list."""
    target = LEGACY_SESSION_HEADER.lower().encode("latin-1")
    return [(key, value) for key, value in headers if key.lower() != target]


def scope_without_session_headers(scope: dict) -> dict:
    """Copy an ASGI scope with incoming session headers removed."""
    copied = dict(scope)
    copied["headers"] = strip_legacy_session_headers_asgi(list(scope.get("headers") or []))
    return copied


def build_mcp_response_headers(
    *,
    content_type: str = MCP_JSON_CONTENT_TYPE,
    protocol_version: str = MODERN_PROTOCOL_VERSION,
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Standard MCP 2026-07-28 response headers."""
    headers = {
        HEADER_CONTENT_TYPE: content_type,
        HEADER_MCP_PROTOCOL_VERSION: protocol_version,
        HEADER_VARY: "Origin",
    }
    if extra:
        headers.update(extra)
    return strip_legacy_session_headers(headers)


def build_sse_stream_headers(
    protocol_version: str = MODERN_PROTOCOL_VERSION,
) -> dict[str, str]:
    """SSE stream headers including proxy buffering guards."""
    return build_mcp_response_headers(
        content_type=MCP_SSE_CONTENT_TYPE,
        protocol_version=protocol_version,
        extra={
            HEADER_X_ACCEL_BUFFERING: "no",
            HEADER_CACHE_CONTROL: "no-transform",
        },
    )


def extract_mcp_param_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Extract Mcp-Param-* mirrored parameters from request headers."""
    params: dict[str, str] = {}
    prefix = HEADER_MCP_PARAM_PREFIX.lower()
    for key, value in headers.items():
        if key.lower().startswith(prefix):
            param_name = key[len(HEADER_MCP_PARAM_PREFIX) :]
            params[param_name] = value
    return params


def extract_mcp_scope_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy MCP scoped request headers. Authorization is not included."""
    scoped: dict[str, str] = {}
    for name in (HEADER_MCP_PROTOCOL_VERSION, HEADER_MCP_METHOD, HEADER_MCP_NAME):
        value = get_header(headers, name)
        if value:
            scoped[name] = value
    scoped.update(extract_mcp_param_headers(headers))
    return scoped
