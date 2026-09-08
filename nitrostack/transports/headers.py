"""MCP 2.0 HTTP header names and builders."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Mapping, Optional

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION

# Request headers
HEADER_CONTENT_TYPE = "Content-Type"
HEADER_MCP_PROTOCOL_VERSION = "MCP-Protocol-Version"
HEADER_MCP_METHOD = "Mcp-Method"
HEADER_MCP_NAME = "Mcp-Name"
HEADER_MCP_PARAM_PREFIX = "Mcp-Param-"
MAX_MCP_PARAM_VALUE_BYTES = 4096
_MCP_PARAM_RESERVED = frozenset({"name", "method", "uri"})
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
CORS_ALLOW_HEADER_NAMES: tuple[str, ...] = (
    "Content-Type",
    "Accept",
    "Authorization",
    "MCP-Protocol-Version",
    "Mcp-Method",
    "Mcp-Name",
    "Last-Event-ID",
    "Mcp-Session-Id",
)
CORS_ALLOW_HEADERS = ", ".join(CORS_ALLOW_HEADER_NAMES)
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


def handled_protocol_version(
    request_headers: Mapping[str, str],
    *,
    fallback: str,
    supported: Optional[Collection[str]] = None,
) -> str:
    """Protocol version that handled the request. Unsupported client values are ignored."""
    header = get_header(request_headers, HEADER_MCP_PROTOCOL_VERSION)
    if isinstance(header, str) and header.strip():
        value = header.strip()
        if supported is None or value in supported:
            return value
    return fallback


def build_mcp_response_headers(
    *,
    content_type: str = MCP_JSON_CONTENT_TYPE,
    protocol_version: str = MODERN_PROTOCOL_VERSION,
    method: Optional[str] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Standard MCP response headers. Echo values win over inner-app extras."""
    headers = {
        HEADER_CONTENT_TYPE: content_type,
        HEADER_VARY: "Origin",
    }
    if extra:
        headers.update(extra)
    headers[HEADER_MCP_PROTOCOL_VERSION] = protocol_version
    if method:
        headers[HEADER_MCP_METHOD] = method
    return strip_legacy_session_headers(headers)


def build_mcp_echo_headers(
    request_headers: Mapping[str, str],
    *,
    protocol_version: str,
    method: Optional[str] = None,
    supported_versions: Optional[Collection[str]] = None,
    content_type: str = MCP_JSON_CONTENT_TYPE,
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Echo the handled protocol version and method on every ``/mcp`` response."""
    echo_method = method or get_header(request_headers, HEADER_MCP_METHOD)
    return build_mcp_response_headers(
        content_type=content_type,
        protocol_version=handled_protocol_version(
            request_headers, fallback=protocol_version, supported=supported_versions
        ),
        method=echo_method,
        extra=extra,
    )


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
    """Extract ``Mcp-Param-*`` values keyed by the header suffix."""
    params: dict[str, str] = {}
    prefix = HEADER_MCP_PARAM_PREFIX.lower()
    for key, value in headers.items():
        lower = key.lower()
        if not lower.startswith(prefix):
            continue
        param_name = key[len(prefix) :]
        if param_name:
            params[param_name] = value
    return params


def first_oversized_mcp_param(headers: Mapping[str, str]) -> Optional[str]:
    """Return the first ``Mcp-Param-*`` suffix whose value exceeds the size cap."""
    for name, value in extract_mcp_param_headers(headers).items():
        if len(str(value).encode("utf-8")) > MAX_MCP_PARAM_VALUE_BYTES:
            return name
    return None


def merge_mcp_param_headers(
    arguments: Mapping[str, Any],
    param_headers: Mapping[str, str],
    *,
    allowed_fields: Optional[set[str]] = None,
    reserved: frozenset[str] = _MCP_PARAM_RESERVED,
) -> dict[str, Any]:
    """
    Copy ``arguments`` and fill missing keys from ``Mcp-Param-*``.

    Existing JSON-RPC values win. ``name`` / ``method`` / ``uri`` are never
    taken from headers. When ``allowed_fields`` is set, unknown suffixes are
    ignored (schema-declared params only).
    """
    merged = dict(arguments)
    reserved_lower = {item.lower() for item in reserved}
    field_map = (
        {field.lower(): field for field in allowed_fields}
        if allowed_fields is not None
        else None
    )
    for raw_name, value in param_headers.items():
        if not raw_name or raw_name.lower() in reserved_lower:
            continue
        dest = field_map.get(raw_name.lower()) if field_map is not None else raw_name
        if dest is None:
            continue
        current = merged.get(dest)
        if dest in merged and current is not None and current != "":
            continue
        merged[dest] = value
    return merged


def extract_mcp_scope_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy MCP scoped request headers. Authorization is not included."""
    scoped: dict[str, str] = {}
    for name in (HEADER_MCP_PROTOCOL_VERSION, HEADER_MCP_METHOD, HEADER_MCP_NAME):
        value = get_header(headers, name)
        if value:
            scoped[name] = value
    prefix = HEADER_MCP_PARAM_PREFIX.lower()
    for key, value in headers.items():
        if key.lower().startswith(prefix) and key.lower() != prefix:
            scoped[key] = value
    return scoped
