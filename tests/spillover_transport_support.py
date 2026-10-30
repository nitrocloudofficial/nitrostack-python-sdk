"""Real application and protocol clients for spillover transport regressions."""

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from starlette.testclient import TestClient

from nitrostack import (
    DataSpilloverInterceptor, FileSpilloverStore, MemorySpilloverStore,
    module, tool, use_interceptors,
)
from nitrostack.core.app import McpApplication, ServerConfig, mcp_app

MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "spillover-client", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class PayloadInput(BaseModel):
    value: Any = None
    case: str | None = None


def large_value(case):
    if case == "threshold":
        return "x" * 65536
    if case == "threshold-plus-one":
        return "x" * 65537
    if case.endswith("MiB"):
        return "x" * (int(case[:-3]) * 1024 * 1024)
    if case == "unicode":
        return "é🚀" * 200000
    if case == "escaped":
        return '\\"\n\t' * 100000
    if case == "rows":
        return [{"row": i, "value": "row"} for i in range(100000)]
    if case == "dictionary":
        return {str(i): i for i in range(50000)}
    if case == "nested":
        return [{"chunks": ["x" * (1024 * 1024)] * 6}]
    return "x" * (10 * 1024 * 1024 + 1)


def build_app(backend, root, era="legacy", threshold=64 * 1024,
              prefix="resource://data-spillover/"):
    options = dict(max_bytes=10 * 1024 * 1024, max_total_bytes=64 * 1024 * 1024)
    store = MemorySpilloverStore(**options) if backend == "memory" else FileSpilloverStore(root, **options)
    interceptor = DataSpilloverInterceptor(threshold, store, prefix)

    class Tools:
        @tool("payload", "Return a payload", PayloadInput, task_support="optional")
        @use_interceptors(interceptor)
        async def payload(self, input, context):
            return large_value(input.case) if input.case is not None else input.value

    @module(name="spillover-wire", providers=[Tools])
    class Root:
        pass

    @mcp_app(module=Root, server=ServerConfig(name="spillover-wire", protocol_era=era))
    class App:
        pass

    return McpApplication(App)


class HttpPeer:
    def __init__(self, client, era="legacy", token=None):
        self.client, self.era = client, era
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        self.next_id = 1
        if era == "legacy":
            response = self.rpc("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "spillover-client", "version": "1"},
            })
            assert "result" in response
            self.headers["mcp-session-id"] = self.last_response.headers["mcp-session-id"]
            initialized = client.post("/mcp", headers=self.headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            assert initialized.status_code == 202
        else:
            self.headers["MCP-Protocol-Version"] = "2026-07-28"

    def rpc(self, method, params):
        params = dict(params)
        headers = {**self.headers, "Mcp-Method": method}
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        elif method == "resources/read":
            headers["Mcp-Name"] = params["uri"]
        if self.era != "legacy":
            params["_meta"] = {**params.get("_meta", {}), **MODERN_META}
        self.last_response = self.client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params,
        })
        self.next_id += 1
        assert self.last_response.status_code == 200, self.last_response.text
        return self.last_response.json()

    def call(self, value, **extra):
        return self.rpc("tools/call", {"name": "payload", "arguments": {"value": value}, **extra})

    def read(self, uri):
        return self.rpc("resources/read", {"uri": uri})


@contextmanager
def http_app(backend, root, **options):
    app = build_app(backend, root, **options)
    with TestClient(app.get_combined_app(json_response=True)) as client:
        yield client


async def stdio_main():
    from nitrostack.transports.stdio import run_stdio, safe_stdio_transport

    with safe_stdio_transport():
        app = build_app(sys.argv[1], sys.argv[2], era="auto", threshold=16)
        await run_stdio(app.mcp_server, "auto")


if __name__ == "__main__":
    asyncio.run(stdio_main())
