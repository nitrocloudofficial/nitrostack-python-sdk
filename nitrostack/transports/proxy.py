"""Trusted reverse-proxy handling for forwarded host and proto.

``X-Forwarded-*`` is ignored unless the direct socket peer is on an explicit
allow-list (``TRUSTED_PROXIES`` / ``MCP_TRUSTED_PROXIES``). ``X-Forwarded-For``
is never used to decide trust.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping, Sequence
from typing import Any, Optional
from urllib.parse import urlparse

from nitrostack.transports.headers import get_header

TRUSTED_PROXIES_ENV = "TRUSTED_PROXIES"
MCP_TRUSTED_PROXIES_ENV = "MCP_TRUSTED_PROXIES"


def configured_trusted_proxies() -> tuple[str, ...]:
    """Comma-separated IPs, CIDRs, or hostnames. Empty means trust nobody."""
    raw = os.environ.get(TRUSTED_PROXIES_ENV) or os.environ.get(MCP_TRUSTED_PROXIES_ENV) or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_peer_ip(peer: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    host = peer.strip()
    if host.startswith("["):
        end = host.find("]")
        host = host[1:end] if end != -1 else host.lstrip("[")
    elif host.count(":") == 1:
        candidate, _, tail = host.rpartition(":")
        if tail.isdigit():
            host = candidate
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def peer_is_trusted(
    peer: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> bool:
    """True when the direct socket peer is on the configured allow-list."""
    allow = tuple(trusted) if trusted is not None else configured_trusted_proxies()
    if not allow or not peer:
        return False
    ip = _parse_peer_ip(peer)
    peer_key = peer.strip().lower()
    for item in allow:
        token = item.strip()
        if not token:
            continue
        if ip is not None:
            try:
                if ip in ipaddress.ip_network(token, strict=False):
                    return True
                continue
            except ValueError:
                pass
        if token.lower() == peer_key or token.lower() == (ip and str(ip)):
            return True
    return False


def first_forwarded_value(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Left-most value of a comma-separated forwarded header."""
    raw = get_header(headers, name)
    if not raw:
        return None
    value = raw.split(",")[0].strip()
    return value or None


def trusted_forwarded_host(
    headers: Mapping[str, str],
    peer: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """``X-Forwarded-Host`` when the peer is trusted; otherwise ignored."""
    if not peer_is_trusted(peer, trusted):
        return None
    return first_forwarded_value(headers, "X-Forwarded-Host")


def trusted_forwarded_proto(
    headers: Mapping[str, str],
    peer: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """``X-Forwarded-Proto`` when the peer is trusted; otherwise ignored."""
    if not peer_is_trusted(peer, trusted):
        return None
    proto = first_forwarded_value(headers, "X-Forwarded-Proto")
    if not proto:
        return None
    proto = proto.split(";")[0].strip().lower()
    if proto in {"http", "https"}:
        return proto
    return None


def hostname_from_host_header(value: str) -> str:
    """Hostname from a ``Host`` / ``X-Forwarded-Host`` value (port stripped)."""
    host = value.strip()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end].lower() if end != -1 else host.lower()
    if host.count(":") == 1:
        name, _, tail = host.rpartition(":")
        if tail.isdigit():
            return name.lower()
    return host.lower()


def request_host_for_cimd(
    headers: Mapping[str, str],
    peer: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Inbound host used for CIMD / OAuth host pins.

    Forwarded host is used only when the direct peer is trusted. Untrusted
    ``X-Forwarded-Host`` cannot rebind the comparison host.
    """
    forwarded = trusted_forwarded_host(headers, peer, trusted)
    if forwarded:
        return hostname_from_host_header(forwarded) or None
    host = get_header(headers, "Host")
    if host:
        return hostname_from_host_header(host) or None
    return None


def cimd_url_matches_request_host(
    cimd_url: str,
    headers: Mapping[str, str],
    peer: Optional[str],
    trusted: Optional[Sequence[str]] = None,
) -> bool:
    """True when the CIMD URL host equals the trusted-proxy-aware request host."""
    expected = (urlparse(cimd_url).hostname or "").lower()
    actual = request_host_for_cimd(headers, peer, trusted)
    return bool(expected and actual and expected == actual)


def public_origin(
    headers: Mapping[str, str],
    peer: Optional[str],
    *,
    fallback_host: Optional[str] = None,
    fallback_proto: str = "http",
    trusted: Optional[Sequence[str]] = None,
) -> str:
    """Absolute origin for health, docs, and OAuth URLs."""
    host = trusted_forwarded_host(headers, peer, trusted) or get_header(headers, "Host") or fallback_host
    proto = trusted_forwarded_proto(headers, peer, trusted) or fallback_proto or "http"
    if not host:
        host = "127.0.0.1"
    return f"{proto}://{host}"


def public_url(
    headers: Mapping[str, str],
    peer: Optional[str],
    path: str = "/mcp",
    **kwargs: Any,
) -> str:
    """``public_origin`` plus a path (OAuth connect URL, health ``publicUrl``)."""
    origin = public_origin(headers, peer, **kwargs).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"


def request_peer(request: Any) -> Optional[str]:
    """Direct socket peer from a Starlette / ASGI request."""
    client = getattr(request, "client", None)
    if client is None:
        return None
    host = getattr(client, "host", None)
    if host:
        return str(host)
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return None


def public_origin_for_request(
    request: Any,
    *,
    fallback_host: Optional[str] = None,
    fallback_proto: Optional[str] = None,
    trusted: Optional[Sequence[str]] = None,
) -> str:
    headers = getattr(request, "headers", None) or {}
    url = getattr(request, "url", None)
    proto = fallback_proto or getattr(url, "scheme", None) or "http"
    host = fallback_host or getattr(url, "netloc", None)
    return public_origin(
        headers,
        request_peer(request),
        fallback_host=host,
        fallback_proto=proto,
        trusted=trusted,
    )


def public_url_for_request(
    request: Any,
    path: str = "/mcp",
    **kwargs: Any,
) -> str:
    origin = public_origin_for_request(request, **kwargs).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"
