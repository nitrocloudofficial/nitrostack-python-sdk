"""Opt-in request tracing, enabled with NITROSTACK_HTTP_DEBUG=1."""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict

from starlette.types import ASGIApp, Receive, Scope, Send

from nitrostack.core.log_emitter import install_log_handler
from nitrostack.transports.http.constants import logger


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
    install_log_handler("nitrostack.transports.http")
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