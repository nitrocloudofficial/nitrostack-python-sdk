"""ASGI middleware for the HTTP transport."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from nitrostack.transports.headers import strip_legacy_session_headers_asgi
from nitrostack.transports.http.constants import MCP_ACCEPT, logger


class JsonBodyLimitMiddleware:
    """Reject oversized JSON HTTP bodies with 413 before the MCP stack parses them."""

    def __init__(self, app: ASGIApp, *, limit_bytes: int) -> None:
        self.app = app
        self.limit_bytes = limit_bytes

    @staticmethod
    def _json_media_type(value: bytes) -> bool:
        media = value.decode("latin-1").split(";", 1)[0].strip().lower()
        if media == "application/json":
            return True
        return media.startswith("application/") and media.endswith("+json")

    @staticmethod
    def _is_json_post(scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        for key, value in scope.get("headers") or []:
            if key.lower() == b"content-type" and JsonBodyLimitMiddleware._json_media_type(value):
                return True
        return False

    async def _reject_too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"error": "Payload too large"}, status_code=413)
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_json_post(scope):
            await self.app(scope, receive, send)
            return

        for key, value in scope.get("headers") or []:
            if key.lower() == b"content-length":
                try:
                    if int(value) > self.limit_bytes:
                        await self._reject_too_large(scope, receive, send)
                        return
                except ValueError:
                    break

        buffered: List[Any] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                buffered.append(message)
                break
            chunk = message.get("body", b"") or b""
            total += len(chunk)
            if total > self.limit_bytes:
                await self._reject_too_large(scope, receive, send)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        replay_index = 0

        async def replay_receive():
            nonlocal replay_index
            if replay_index < len(buffered):
                message = buffered[replay_index]
                replay_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)


class ExactEndpointSlashMiddleware:
    """Internally rewrite `/mcp` -> `/mcp/` so Starlette does not 307."""

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
    """Normalize Accept and version headers; JSON-RPC stays with the MCP manager."""

    def __init__(self, app: ASGIApp, *, drop_session_headers: bool = False,
                 require_protocol_version: bool = False) -> None:
        self.app = app
        self.drop_session_headers = drop_session_headers
        self.require_protocol_version = require_protocol_version

    @staticmethod
    def _normalize_accept(value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return MCP_ACCEPT
        media_types = [m.strip() for m in value.split(",")]
        has_json = any(m.startswith("application/json") for m in media_types)
        has_sse = any(m.startswith("text/event-stream") for m in media_types)
        if has_json and has_sse:
            return None
        if any(m.startswith("*/*") for m in media_types):
            return MCP_ACCEPT
        return None

    @staticmethod
    def _canonicalize_protocol_version(headers: List[Any]) -> List[Any]:
        """Normalize casing without hiding duplicates from MCP's validator."""
        version_values: List[bytes] = []
        kept: List[Any] = []
        for key, value in headers:
            if key.lower() == b"mcp-protocol-version":
                version_values.append(value)
            else:
                kept.append((key, value))
        if not version_values:
            return headers
        kept.extend((b"mcp-protocol-version", v) for v in version_values)
        return kept

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: List[Any] = list(scope.get("headers") or [])
        if self.require_protocol_version and scope.get("method") == "POST" and not any(
            key.lower() == b"mcp-protocol-version" and value.strip() for key, value in headers
        ):
            response = JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32020, "message": "MCP-Protocol-Version header is required",
                }}, status_code=400,
            )
            await response(scope, receive, send)
            return
        if self.drop_session_headers:
            headers = strip_legacy_session_headers_asgi(headers)
        headers = self._canonicalize_protocol_version(headers)

        raw_accept = next((v for k, v in headers if k.lower() == b"accept"), None)
        accept = self._normalize_accept(raw_accept.decode("latin-1") if raw_accept is not None else None)
        if accept is not None:
            headers = [(k, v) for k, v in headers if k.lower() != b"accept"]
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