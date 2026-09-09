"""Concurrent identical JSON-RPC ids stay isolated on the sessionless path."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.runtime.correlation import InFlightRegistry, new_correlation_id

CALL_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
    "mcp-method": "tools/call",
    "mcp-protocol-version": "2026-07-28",
}

CALL_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class EchoInput(BaseModel):
    value: str = Field(default="")


def test_registry_cancel_targets_correlation_id_only():
    registry = InFlightRegistry()
    first = new_correlation_id()
    second = new_correlation_id()
    registry.register(first, jsonrpc_id=1)
    registry.register(second, jsonrpc_id=1)
    assert len(registry) == 2
    assert {ticket.jsonrpc_id for ticket in registry} == {1}
    assert registry.cancel(first) is True
    assert registry.get(first).cancel_requested.is_set()
    assert not registry.get(second).cancel_requested.is_set()
    registry.discard(first)
    assert len(registry) == 1
    assert registry.get(second) is not None


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


async def _asgi_json_post(app, payload: dict, headers: dict[str, str]) -> tuple[int, dict]:
    status = 0
    chunks: list[bytes] = []
    sent_request = False
    finished = asyncio.Event()
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body") or b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": _header_list(headers),
        "client": ("testclient", 50000),
        "server": ("test", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    finally:
        finished.set()
    raw = b"".join(chunks)
    parsed = json.loads(raw.decode("utf-8")) if raw else {}
    return status, parsed


def _call_body(name: str, value: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": {"value": value},
            "_meta": CALL_META,
        },
    }


def _make_overlap_app():
    started_alpha = asyncio.Event()
    started_beta = asyncio.Event()
    release = asyncio.Event()
    seen: list[str] = []

    @injectable()
    class PairController:
        @tool(name="alpha", description="alpha", input_schema=EchoInput)
        async def alpha(self, input: EchoInput, context: ExecutionContext) -> str:
            seen.append(f"{context.correlation_id}:{context.jsonrpc_id}:{input.value}")
            started_alpha.set()
            await release.wait()
            return f"alpha:{input.value}"

        @tool(name="beta", description="beta", input_schema=EchoInput)
        async def beta(self, input: EchoInput, context: ExecutionContext) -> str:
            seen.append(f"{context.correlation_id}:{context.jsonrpc_id}:{input.value}")
            started_beta.set()
            await release.wait()
            return f"beta:{input.value}"

    @module(name="overlap-rpc", controllers=[PairController])
    class PairModule:
        pass

    @mcp_app(module=PairModule, server=ServerConfig(name="overlap-rpc"))
    class App:
        pass

    app = asyncio.run(McpApplicationFactory.create(App))
    return app, started_alpha, started_beta, release, seen


def _tool_text(payload: dict) -> str:
    result = payload.get("result") or {}
    content = result.get("content") or []
    if content:
        return str(content[0].get("text") or "")
    structured = result.get("structuredContent")
    if structured is not None:
        return str(structured)
    return json.dumps(payload)


def test_two_overlapping_id_1_calls_return_distinct_results(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    app, started_alpha, started_beta, release, seen = _make_overlap_app()
    http_app = app.get_combined_app(json_response=True)

    async def run():
        async def both():
            first = asyncio.create_task(
                _asgi_json_post(
                    http_app,
                    _call_body("alpha", "one"),
                    {**CALL_HEADERS, "mcp-name": "alpha"},
                )
            )
            second = asyncio.create_task(
                _asgi_json_post(
                    http_app,
                    _call_body("beta", "two"),
                    {**CALL_HEADERS, "mcp-name": "beta"},
                )
            )
            await asyncio.wait_for(started_alpha.wait(), timeout=2)
            await asyncio.wait_for(started_beta.wait(), timeout=2)
            assert len(app._in_flight) == 2
            assert {ticket.jsonrpc_id for ticket in app._in_flight} == {1}
            correlations = [ticket.correlation_id for ticket in app._in_flight]
            assert correlations[0] != correlations[1]
            release.set()
            return await asyncio.gather(first, second)

        return await _with_lifespan(http_app, both)

    (status_a, body_a), (status_b, body_b) = asyncio.run(run())
    assert status_a == 200 and status_b == 200
    assert body_a.get("id") == 1
    assert body_b.get("id") == 1
    texts = {_tool_text(body_a), _tool_text(body_b)}
    assert "alpha:one" in texts
    assert "beta:two" in texts
    assert len({row.split(":")[0] for row in seen}) == 2


def test_cancel_of_one_id_1_call_does_not_cancel_the_other(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    app, started_alpha, started_beta, release, seen = _make_overlap_app()
    http_app = app.get_combined_app(json_response=True)

    async def run_cancel():
        async def both():
            first = asyncio.create_task(
                _asgi_json_post(
                    http_app,
                    _call_body("alpha", "one"),
                    {**CALL_HEADERS, "mcp-name": "alpha"},
                )
            )
            second = asyncio.create_task(
                _asgi_json_post(
                    http_app,
                    _call_body("beta", "two"),
                    {**CALL_HEADERS, "mcp-name": "beta"},
                )
            )
            await asyncio.wait_for(started_alpha.wait(), timeout=2)
            await asyncio.wait_for(started_beta.wait(), timeout=2)
            alpha_corr = next(row.split(":")[0] for row in seen if row.endswith(":one"))
            assert app._in_flight.cancel(alpha_corr) is True
            first.cancel()
            release.set()
            return await second

        return await _with_lifespan(http_app, both)

    status, body = asyncio.run(run_cancel())
    assert status == 200
    assert body.get("id") == 1
    assert "beta:two" in _tool_text(body)
