import sys
import contextlib
import uuid
import contextvars
from typing import TYPE_CHECKING, Any, Generator, Mapping

import anyio
from anyio.abc import ObjectReceiveStream, ObjectSendStream
import mcp.types as types
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp_types import PROTOCOL_VERSION_META_KEY, UNSUPPORTED_PROTOCOL_VERSION

from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION, ProtocolEra
from nitrostack.runtime.request_ctx import stdio_connection_id

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server


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
    """Apply era policy and preserve the trusted connection identity and sender context."""

    def __init__(self, stream: Any, connection_id: str, write_stream: Any, era: ProtocolEra) -> None:
        self._stream = stream
        self._connection_id = connection_id
        self._write_stream = write_stream
        self._era = era
        self.last_context = None

    async def receive(self):
        while True:
            message = await self._stream.receive()
            error = self._era_error(message)
            if error is None:
                break
            await self._write_stream.send(SessionMessage(types.JSONRPCError(
                jsonrpc="2.0", id=message.message.id, error=error,
            )))
        sender_context = getattr(self._stream, "last_context", None)
        self.last_context = sender_context.copy() if sender_context is not None else contextvars.copy_context()
        self.last_context.run(stdio_connection_id.set, self._connection_id)
        return message

    def _era_error(self, message: SessionMessage | Exception) -> types.ErrorData | None:
        # Server.run negotiates from the first request. Refuse incompatible requests
        # before negotiation so a rejected handshake cannot select the wrong era.
        # Accepted requests, notifications, responses and parse errors are passed
        # unchanged to the official server; this adapter never dispatches handlers.
        if not isinstance(message, SessionMessage) or not isinstance(message.message, types.JSONRPCRequest):
            return None
        request = message.message
        if self._era == "modern" and request.method == "initialize":
            return types.ErrorData(code=types.METHOD_NOT_FOUND, message="Method not found: initialize")
        params = request.params or {}
        meta = params.get("_meta")
        version = meta.get(PROTOCOL_VERSION_META_KEY) if isinstance(meta, Mapping) else None
        if self._era == "modern" and version != MODERN_PROTOCOL_VERSION:
            return types.ErrorData(
                code=UNSUPPORTED_PROTOCOL_VERSION,
                message="Modern stdio requests require a supported protocol version in params._meta",
                data={"supportedVersions": [MODERN_PROTOCOL_VERSION]},
            )
        if self._era == "legacy" and version == MODERN_PROTOCOL_VERSION:
            return types.ErrorData(
                code=types.INVALID_REQUEST,
                message="Legacy stdio does not accept modern protocol envelopes",
            )
        return None

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
    read_stream: "ObjectReceiveStream[SessionMessage | Exception]",
    write_stream: "ObjectSendStream[SessionMessage]",
    era: ProtocolEra,
) -> None:
    """Serve one connection with a trusted identity shared by its requests."""
    connection_id = uuid.uuid4().hex
    token = stdio_connection_id.set(connection_id)
    try:
        await server.run(
            _StdioConnectionStream(read_stream, connection_id, write_stream, era),
            write_stream,
            server.create_initialization_options(),
        )
    finally:
        stdio_connection_id.reset(token)


async def run_stdio(server: "Server[Any]", era: ProtocolEra) -> None:
    """Serve official mcp 2.x on process stdin/stdout for the active era."""
    server.protocol_era = era
    async with stdio_server() as (read_stream, write_stream):
        await serve_stdio_streams(server, read_stream, write_stream, era)
