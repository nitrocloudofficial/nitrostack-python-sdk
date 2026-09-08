"""CORS configuration for stateless MCP HTTP."""

from __future__ import annotations

import os
from typing import Mapping, Optional, Sequence

from nitrostack.transports.headers import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_EXPOSE_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_METHODS,
    HEADER_ACCESS_CONTROL_ALLOW_ORIGIN,
    HEADER_ACCESS_CONTROL_EXPOSE_HEADERS,
    HEADER_MCP_PARAM_PREFIX,
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
