import sys
import contextlib
import uuid
import contextvars
from typing import TYPE_CHECKING, Any, Generator

import anyio
from mcp.server.runner import serve_loop
from mcp.server.stdio import stdio_server

from nitrostack.protocol.version import ProtocolEra
from nitrostack.runtime.request_ctx import stdio_connection_id

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server
    from mcp.shared._stream_protocols import ReadStream, WriteStream
    from mcp.shared.message import SessionMessage


class SafeStdoutWrapper:
    """
    Wrapper for sys.stdout that redirects all text writes to sys.stderr,
    but preserves sys.stdout.buffer for binary JSON-RPC transport frames.
    """
    def __init__(self, original_stdout):
        self._original = original_stdout
        # Keep original binary buffer intact for MCP JSON-RPC transport
        self.buffer = original_stdout.buffer

    def write(self, data: str) -> int:
        sys.stderr.write(data)
        sys.stderr.flush()
        return len(data)

    def flush(self) -> None:
        sys.stderr.flush()

    def __getattr__(self, name: str):
        return getattr(self._original, name)

@contextlib.contextmanager
def safe_stdio_transport() -> Generator[None, None, None]:
    """Context manager that safely wraps sys.stdout during stdio transport execution."""
    original_stdout = sys.stdout
    sys.stdout = SafeStdoutWrapper(original_stdout)
    try:
        yield
    finally:
        sys.stdout = original_stdout


class _StdioConnectionStream:
    """Preserve sender context while binding a server-owned connection identity."""

    def __init__(self, stream: Any, connection_id: str) -> None:
        self._stream = stream
        self._connection_id = connection_id
        self.last_context = None

    async def receive(self):
        message = await self._stream.receive()
        sender_context = getattr(self._stream, "last_context", None)
        self.last_context = sender_context.copy() if sender_context is not None else contextvars.copy_context()
        self.last_context.run(stdio_connection_id.set, self._connection_id)
        return message

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._stream.__aexit__(*args)

    async def aclose(self):
        await self._stream.aclose()


async def serve_stdio_streams(
    server: "Server[Any]",
    read_stream: "ReadStream[SessionMessage | Exception]",
    write_stream: "WriteStream[SessionMessage]",
    era: ProtocolEra,
) -> None:
    """Serve one connection with a trusted identity shared by its requests."""
    connection_id = uuid.uuid4().hex
    token = stdio_connection_id.set(connection_id)
    try:
        await _serve_stdio_connection(
            server, _StdioConnectionStream(read_stream, connection_id), write_stream, era
        )
    finally:
        stdio_connection_id.reset(token)


async def _serve_stdio_connection(
    server: "Server[Any]",
    read_stream: "ReadStream[SessionMessage | Exception]",
    write_stream: "WriteStream[SessionMessage]",
    era: ProtocolEra,
) -> None:
    """Drive official mcp 2.x over an already-open stdio stream pair.

    ``auto`` uses the official dual-era loop (2026 envelope or 2025 handshake).
    ``modern`` rejects ``initialize``. ``legacy`` keeps the handshake loop.
    """
    init_options = server.create_initialization_options()
    if era == "auto":
        await server.run(read_stream, write_stream, init_options)
        return

    async with server.lifespan(server) as lifespan_state:
        try:
            if era == "modern":
                # A per-request envelope loop (no `initialize` handshake) built from
                # documented, public `mcp.server.runner` building blocks, so this does
                # not depend on a leading-underscore internal that mcp is free to
                # remove without notice.
                from mcp.server.runner import (
                    Connection,
                    JSONRPCDispatcher,
                    LATEST_MODERN_VERSION,
                    NotifyOnlyOutbound,
                    ServerRunner,
                    aclose_shielded,
                    modern_on_request,
                )

                dispatcher: JSONRPCDispatcher = JSONRPCDispatcher(read_stream, write_stream)
                outbound = NotifyOnlyOutbound(dispatcher)

                async def _on_notify(dctx, method, params):
                    # Fresh per-notification `Connection`, mirroring the request path:
                    # notifications carry no envelope of their own at this era.
                    connection = Connection.from_envelope(
                        LATEST_MODERN_VERSION, None, None, outbound=outbound
                    )
                    try:
                        await ServerRunner(server, connection, lifespan_state).on_notify(
                            dctx, method, params
                        )
                    finally:
                        await aclose_shielded(connection)

                await dispatcher.run(modern_on_request(server, lifespan_state), _on_notify)
            else:
                await serve_loop(
                    server,
                    read_stream,
                    write_stream,
                    lifespan_state=lifespan_state,
                    init_options=init_options,
                )
        finally:
            await write_stream.aclose()


async def run_stdio(server: "Server[Any]", era: ProtocolEra) -> None:
    """Serve official mcp 2.x on process stdin/stdout for the active era."""
    server.protocol_era = era
    async with stdio_server() as (read_stream, write_stream):
        await serve_stdio_streams(server, read_stream, write_stream, era)
