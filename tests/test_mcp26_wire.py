import asyncio
import json

import mcp.types as types
import pytest
from mcp import MCPError
from pydantic import BaseModel
from starlette.testclient import TestClient

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.testing import NitroTestingModule

MODERN_VERSION = "2026-07-28"


class In(BaseModel):
    q: str = ""


@injectable()
class C:
    @tool(name="alpha", description="first", input_schema=In, task_support="optional")
    async def alpha(self, input: In, context: ExecutionContext) -> str:
        return "a"


@module(name="m", controllers=[C])
class M:
    pass


def make_app(monkeypatch, era):
    monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", era)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    harness = asyncio.run(NitroTestingModule.create(M))
    return harness.app


def envelope(extra=None):
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    params = dict(extra or {})
    params["_meta"] = {**params.get("_meta", {}), **meta}
    return params


def rpc(client, method, params=None, *, headers=None, id_=1, envelope=True):
    base = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    base.update(headers or {})
    p = dict(params or {})
    if envelope:
        meta = dict(p.get("_meta") or {})
        meta.setdefault("io.modelcontextprotocol/protocolVersion", MODERN_VERSION)
        meta.setdefault("io.modelcontextprotocol/clientCapabilities", {})
        p["_meta"] = meta
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": p}
    return client.post("/mcp", content=json.dumps(body), headers=base)

def body_of(resp):
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def modern_headers(method, name=None):
    h = {"MCP-Protocol-Version": MODERN_VERSION, "Mcp-Method": method}
    if name:
        h["Mcp-Name"] = name
    return h


def error_code(payload):
    return (payload.get("error") or {}).get("code")


def exc_code(exc):
    err = getattr(exc, "error", None)
    return getattr(err, "code", getattr(exc, "code", None))


@pytest.mark.parametrize("era", ["modern", "auto"])
def test_ping_is_method_not_found(monkeypatch, era):
    app = make_app(monkeypatch, era)
    with TestClient(app.get_combined_app()) as client:
        resp = rpc(client, "ping", headers=modern_headers("ping"))
    assert error_code(body_of(resp)) == -32601
def test_ping_handler_not_found_on_2026(monkeypatch):
    app = make_app(monkeypatch, "modern")
    handler = app.mcp_server._request_handlers["ping"]  # ADAPT: find where add_request_handler stores it


@pytest.mark.parametrize("era", ["modern", "auto"])
def test_discover_carries_server_info_in_meta(monkeypatch, era):
    app = make_app(monkeypatch, era)
    with TestClient(app.get_combined_app()) as client:
        resp = rpc(client, "server/discover", headers=modern_headers("server/discover"))
    assert resp.status_code == 200
    assert "mcp-session-id" not in {k.lower() for k in resp.headers}
    result = body_of(resp)["result"]
    info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] and info["version"]
    assert result["resultType"] == "complete"


def test_discover_keeps_cache_hint_in_meta(monkeypatch):
    app = make_app(monkeypatch, "modern")
    result = app.handle_server_discover()
    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]
    assert len(result["_meta"]) >= 2  # serverInfo plus the existing cache hint


def test_initialize_is_not_served_on_modern(monkeypatch):
    app = make_app(monkeypatch, "modern")
    with TestClient(app.get_combined_app()) as client:
        resp = rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            headers=modern_headers("initialize"),
        )
    assert error_code(body_of(resp)) == -32601


def test_tools_call_without_mcp_method_header_fails_closed(monkeypatch):
    app = make_app(monkeypatch, "modern")
    with TestClient(app.get_combined_app()) as client:
        resp = rpc(
            client,
            "tools/call",
            {"name": "alpha", "arguments": {}},
            headers={"MCP-Protocol-Version": MODERN_VERSION},
        )
    assert resp.status_code >= 400 or error_code(body_of(resp)) == -32020


def test_missing_client_capability_code_is_defined():
    assert -32021 in {int(member.value) for member in JsonRpcErrorCode}


def test_task_call_returns_32021_when_tasks_unavailable(monkeypatch):
    app = make_app(monkeypatch, "modern")
    app.mcp_server.has_task_support = False  # what the tasks issue (#25) will do on 2026

    async def go():
        return await app._call_tool("alpha", {}, task=types.TaskMetadata(ttl=1000))

    with pytest.raises(MCPError) as excinfo:
        asyncio.run(go())
    assert exc_code(excinfo.value) == -32021


def test_task_call_still_works_when_tasks_available(monkeypatch):
    app = make_app(monkeypatch, "legacy")

    async def go():
        return await app._call_tool("alpha", {}, task=types.TaskMetadata(ttl=1000))

    assert isinstance(asyncio.run(go()), types.CreateTaskResult)
