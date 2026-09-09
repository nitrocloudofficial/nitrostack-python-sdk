"""MCP 2026 ``/subscriptions/listen`` attach path."""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.auth.jwt import JWTModule, JWTService
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.transports.headers import SSE_SUBSCRIPTIONS_PATH

SSE_HEADERS = {"Accept": "text/event-stream"}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class EchoInput(BaseModel):
    value: str = Field(default="")


def _make_app(name: str = "listen-app"):
    @injectable()
    class EchoController:
        @tool(name="echo", description="echo", input_schema=EchoInput)
        async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
            return input.value

    @module(name=name, controllers=[EchoController])
    class EchoModule:
        pass

    @mcp_app(module=EchoModule, server=ServerConfig(name=name))
    class App:
        pass

    return asyncio.run(McpApplicationFactory.create(App))


def _header_list(headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()]


async def _with_lifespan(app, http_call):
    started = asyncio.Event()
    messages: asyncio.Queue = asyncio.Queue()
    await messages.put({"type": "lifespan.startup"})

    async def receive():
        return await messages.get()

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            started.set()

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await asyncio.wait_for(started.wait(), timeout=2)
    try:
        return await http_call()
    finally:
        await messages.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(task, timeout=2)


async def _asgi_listen(
    app,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    after_start=None,
) -> tuple[int, dict[str, str], bytes]:
    async def http_call():
        status = 0
        response_headers: dict[str, str] = {}
        chunks: list[bytes] = []
        sent_request = False
        allow_disconnect = asyncio.Event()

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            await allow_disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers.update(
                    {
                        key.decode("latin-1"): value.decode("latin-1")
                        for key, value in message.get("headers") or []
                    }
                )
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body") or b"")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": SSE_SUBSCRIPTIONS_PATH,
            "raw_path": SSE_SUBSCRIPTIONS_PATH.encode("ascii"),
            "query_string": b"",
            "headers": _header_list(headers or SSE_HEADERS),
            "client": ("testclient", 50000),
            "server": ("test", 80),
        }
        request_task = asyncio.create_task(app(scope, receive, send))
        for _ in range(200):
            if status and chunks:
                break
            if request_task.done():
                break
            await asyncio.sleep(0.01)
        if after_start is not None:
            await after_start()
            for _ in range(100):
                if b"list_changed" in b"".join(chunks):
                    break
                await asyncio.sleep(0.01)
        allow_disconnect.set()
        if not request_task.done():
            request_task.cancel()
            try:
                await request_task
            except asyncio.CancelledError:
                pass
        else:
            await request_task
        return status, response_headers, b"".join(chunks)

    return await _with_lifespan(app, http_call)


def test_modern_and_auto_listen_attach_and_ack(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    app = _make_app("listen-auto")
    http_app = app.get_combined_app(json_response=True)
    status, headers, body = asyncio.run(_asgi_listen(http_app))
    assert status == 200
    assert "text/event-stream" in headers.get("content-type", "")
    assert b"notifications/subscriptions/acknowledged" in body
    assert b"toolsListChanged" in body


def test_modern_listen_route(monkeypatch):
    monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "modern")
    try:
        app = _make_app("listen-modern")
        http_app = app.get_combined_app(json_response=True)
        status, headers, body = asyncio.run(_asgi_listen(http_app))
        assert status == 200
        assert "text/event-stream" in headers.get("content-type", "")
        assert b"subscriptions/acknowledged" in body
    finally:
        os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


def test_legacy_listen_is_absent(monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "legacy")
    try:
        app = _make_app("listen-legacy")
        http_app = app.get_combined_app(json_response=True)
        with TestClient(http_app) as client:
            response = client.get(SSE_SUBSCRIPTIONS_PATH, headers=SSE_HEADERS)
        assert response.status_code == 404
    finally:
        os.environ.pop("NITRO_MCP_PROTOCOL_VERSION", None)


def test_listen_forwards_bus_events(monkeypatch):
    from mcp.shared.subscriptions import ToolsListChanged

    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    app = _make_app("listen-bus")
    bus = app.mcp_server.subscription_bus
    http_app = app.get_combined_app(json_response=True)

    async def publish():
        await bus.publish(ToolsListChanged())

    status, _headers, body = asyncio.run(_asgi_listen(http_app, after_start=publish))
    assert status == 200
    assert b"notifications/subscriptions/acknowledged" in body
    assert b"notifications/tools/list_changed" in body


def test_unauthenticated_listen_denied_when_jwt_configured(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "listen-jwt")
    JWTModule.for_root(secret_env_var="JWT_SECRET", audience="mcp", issuer="nitro")
    token = DIContainer.get_instance().resolve(JWTService).create_token({"sub": "ada"})
    app = _make_app("listen-jwt")

    denied = asyncio.run(_asgi_listen(app.get_combined_app(json_response=True)))
    assert denied[0] == 401
    assert b"unauthorized" in denied[2]

    status, headers, body = asyncio.run(
        _asgi_listen(
            app.get_combined_app(json_response=True),
            headers={**SSE_HEADERS, "Authorization": f"Bearer {token}"},
        )
    )
    assert status == 200
    assert "text/event-stream" in headers.get("content-type", "")
    assert b"subscriptions/acknowledged" in body


def test_official_listen_handler_is_registered():
    app = _make_app("listen-handler")
    handlers = getattr(app.mcp_server, "_request_handlers", {})
    assert "subscriptions/listen" in handlers
    assert app.mcp_server.listen_handler is not None
    assert app.mcp_server.subscription_bus is not None
