"""Client ID Metadata Document (CIMD) resolution with SSRF defenses."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from nitrostack.protocol.constants import MAX_CIMD_BYTES

CIMD_FETCH_TIMEOUT_SEC = 5.0
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_BLOCKED_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

_BLOCKED_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "100::/64",
        "2001:db8::/32",
    )
)


class CimdValidationError(ValueError):
    """Raised when a client identifier URL or metadata document is invalid."""


class CimdFetchError(ValueError):
    """Raised when a CIMD document cannot be fetched safely."""


def validate_client_identifier_url(client_id_url: str, *, allow_loopback: bool = False) -> str:
    """
    Validate a CIMD ``client_id`` URL before any network fetch.

    Returns the normalized URL string on success.
    """
    if not client_id_url or not isinstance(client_id_url, str):
        raise CimdValidationError("client_id must be a non-empty URL string")

    parsed = urllib.parse.urlparse(client_id_url.strip())
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()

    if scheme == "https":
        pass
    elif scheme == "http" and allow_loopback and hostname in _LOOPBACK_HOSTS:
        pass
    else:
        raise CimdValidationError("client_id must use https:// (or http:// on loopback when allowed)")

    if parsed.username or parsed.password:
        raise CimdValidationError("client_id URL must not contain userinfo")

    if parsed.fragment:
        raise CimdValidationError("client_id URL must not contain a fragment")

    path = parsed.path or ""
    if not path or path == "/":
        raise CimdValidationError("client_id URL must include a non-root path")

    segments = [segment for segment in path.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise CimdValidationError("client_id URL path must not contain '.' or '..' segments")

    if not hostname:
        raise CimdValidationError("client_id URL must include a hostname")

    return client_id_url.strip()


def is_blocked_ip(ip_str: str) -> bool:
    """Return True when ``ip_str`` resolves to a RFC 6890 special-use address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in network for network in _BLOCKED_IPV4_NETWORKS)

    if ip.ipv4_mapped is not None:
        return is_blocked_ip(str(ip.ipv4_mapped))

    if any(ip in network for network in _BLOCKED_IPV6_NETWORKS):
        return True

    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved


async def assert_safe_fetch_target(url_str: str, *, allow_loopback: bool = False) -> None:
    """DNS pre-resolution and IP range filtering."""
    validate_client_identifier_url(url_str, allow_loopback=allow_loopback)
    parsed = urllib.parse.urlparse(url_str)
    hostname = parsed.hostname
    if not hostname:
        raise CimdFetchError(f"Invalid hostname in URL: {url_str}")

    if allow_loopback and hostname.lower() in _LOOPBACK_HOSTS:
        return

    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM),
        )
    except socket.gaierror as exc:
        raise CimdFetchError(f"DNS resolution failed for {hostname}: {exc}") from exc

    resolved_ips = {info[4][0] for info in addr_info if info and info[4]}
    if not resolved_ips:
        raise CimdFetchError(f"DNS resolution returned no addresses for {hostname}")

    for ip in resolved_ips:
        if is_blocked_ip(ip):
            raise CimdFetchError(f"Destination {hostname} resolved to blocked IP: {ip}")


def _fetch_cimd_bytes(url_str: str, *, timeout_sec: float) -> bytes:
    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(
                url_str,
                code,
                "HTTP redirects are not allowed for CIMD fetch",
                headers,
                fp,
            )

    opener = urllib.request.build_opener(_NoRedirectHandler)
    request = urllib.request.Request(url_str, headers={"Accept": "application/json"}, method="GET")

    try:
        with opener.open(request, timeout=timeout_sec) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_CIMD_BYTES:
                        raise CimdFetchError(f"CIMD exceeds maximum size of {MAX_CIMD_BYTES} bytes")
                except ValueError as exc:
                    raise CimdFetchError("Invalid Content-Length header") from exc

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(MAX_CIMD_BYTES - total + 1)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_CIMD_BYTES:
                    raise CimdFetchError(f"CIMD exceeds maximum size of {MAX_CIMD_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise CimdFetchError("HTTP redirects are not allowed for CIMD fetch") from exc
        raise CimdFetchError(f"CIMD fetch failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CimdFetchError(f"CIMD fetch failed: {exc.reason}") from exc


def _validate_cimd_document(doc: Any, fetched_url: str) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise CimdValidationError("CIMD document must be a JSON object")
    if doc.get("client_id") != fetched_url:
        raise CimdValidationError(
            f"CIMD client_id '{doc.get('client_id')}' does not match URL '{fetched_url}'"
        )
    return doc


async def resolve_cimd(
    client_id_url: str,
    *,
    allow_loopback: bool = False,
    timeout_sec: float = CIMD_FETCH_TIMEOUT_SEC,
) -> dict[str, Any]:
    """
    Fetch and validate a Client ID Metadata Document.

    Applies URL validation, DNS/IP filtering, redirect blocking, timeout,
    payload size bounding, and anti-impersonation ``client_id`` checks.
    """
    normalized = validate_client_identifier_url(client_id_url, allow_loopback=allow_loopback)
    await assert_safe_fetch_target(normalized, allow_loopback=allow_loopback)

    body = await asyncio.to_thread(_fetch_cimd_bytes, normalized, timeout_sec=timeout_sec)
    if len(body) > MAX_CIMD_BYTES:
        raise CimdFetchError(f"CIMD exceeds maximum size of {MAX_CIMD_BYTES} bytes")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CimdValidationError("CIMD document must be valid UTF-8 JSON") from exc

    return _validate_cimd_document(document, normalized)
