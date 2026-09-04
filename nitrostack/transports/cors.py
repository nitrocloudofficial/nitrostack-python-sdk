"""CORS configuration for stateless MCP HTTP (Doc 01 §2.3)."""

from __future__ import annotations

from typing import Mapping, Optional

from nitrostack.transports.headers import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_EXPOSE_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_HEADERS,
    HEADER_ACCESS_CONTROL_ALLOW_METHODS,
    HEADER_ACCESS_CONTROL_ALLOW_ORIGIN,
    HEADER_ACCESS_CONTROL_EXPOSE_HEADERS,
    get_header,
)


def build_cors_headers(
    origin: Optional[str] = None,
    *,
    allow_origin: str = "*",
) -> dict[str, str]:
    """Build CORS headers for MCP browser clients (SEP-2243 & SEP-2575)."""
    resolved_origin = origin if origin else allow_origin
    return {
        HEADER_ACCESS_CONTROL_ALLOW_ORIGIN: resolved_origin,
        HEADER_ACCESS_CONTROL_ALLOW_METHODS: CORS_ALLOW_METHODS,
        HEADER_ACCESS_CONTROL_ALLOW_HEADERS: CORS_ALLOW_HEADERS,
        HEADER_ACCESS_CONTROL_EXPOSE_HEADERS: CORS_EXPOSE_HEADERS,
    }


def cors_preflight_response_headers(request_headers: Mapping[str, str]) -> dict[str, str]:
    """Headers for OPTIONS preflight — HTTP 204 No Content (Doc 01 §2.3)."""
    origin = get_header(request_headers, "Origin")
    return build_cors_headers(origin=origin or "*")
