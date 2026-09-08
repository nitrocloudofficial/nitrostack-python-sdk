"""
Dual transport mode (Phase 3): runs STDIO and Streamable HTTP concurrently.

Both transports run as `anyio` tasks in the *same* thread/event loop rather
than a background OS thread. This matters for two reasons:

1. `DIContainer` singletons are shared automatically — a tool call over HTTP
   and one over STDIO hit the same provider instances, with no cross-thread
   synchronization needed.
2. uvicorn's `Server.serve()` only installs SIGTERM/SIGINT handlers when
   running on the main thread (see `uvicorn.Server.capture_signals`); running
   it in a background thread — the previous implementation — silently
   disabled graceful shutdown for the HTTP side.

Shutdown is coordinated: whichever transport stops first (STDIO hitting EOF,
or HTTP receiving a termination signal) cancels the other via the shared
task group's cancel scope, so the process exits cleanly instead of hanging
on one transport forever.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anyio
import uvicorn

from nitrostack.transports.stdio import safe_stdio_transport

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication

logger = logging.getLogger("nitrostack.transports.dual")


async def run_dual(
    mcp_app: "McpApplication",
    http_app: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 3000,
    graceful_timeout: float = 10.0,
) -> None:
    """Run STDIO and Streamable HTTP concurrently until either one stops."""
    config = uvicorn.Config(
        http_app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=graceful_timeout,
    )
    server = uvicorn.Server(config)

    async with anyio.create_task_group() as tg:
        async def _run_http() -> None:
            try:
                await server.serve()
                logger.info("Dual mode: HTTP transport stopped")
            finally:
                # HTTP stopping (e.g. a real SIGTERM) should also stop STDIO.
                # STDIO has no cooperative "please stop" flag of its own, so
                # cancellation is the only option here — acceptable since it's
                # just a passive reader with no in-flight work to drain.
                tg.cancel_scope.cancel()

        async def _run_stdio_guarded() -> None:
            try:
                with safe_stdio_transport():
                    await mcp_app._run_stdio()
                logger.info("Dual mode: STDIO transport stopped")
            finally:
                # STDIO stopping should gracefully drain the HTTP side rather
                # than abruptly cancelling it out from under in-flight
                # requests. Setting `should_exit` lets uvicorn run its own
                # graceful shutdown (respecting `timeout_graceful_shutdown`)
                # and return `serve()` on its own; the task group then exits
                # naturally once both tasks have completed.
                server.should_exit = True

        tg.start_soon(_run_http)
        tg.start_soon(_run_stdio_guarded)
