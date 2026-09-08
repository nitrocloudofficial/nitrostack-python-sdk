"""Envelope keys are stripped from tool arguments before user handlers."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import mcp.types as types
from pydantic import BaseModel, ConfigDict, Field
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.auth.jwt import JWTService
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.task import TaskStatus
from nitrostack.protocol.meta import MCP_META_PREFIX, strip_tool_arguments

CALL_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
    "Mcp-Method": "tools/call",
    "Mcp-Name": "echo",
}


class LooseInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


def test_strip_drops_meta_and_namespaced_keys():
    cleaned = strip_tool_arguments(
        {
            "value": "ok",
            "_meta": {"userId": "eve", "auth": {"token": "secret"}},
            f"{MCP_META_PREFIX}auth": {"userId": "mallory"},
            f"{MCP_META_PREFIX}protocolVersion": "2026-07-28",
        }
    )
    assert cleaned == {"value": "ok"}
    assert "_meta" not in cleaned
    assert all(not str(key).startswith(MCP_META_PREFIX) for key in cleaned)


def test_strip_drops_envelope_keys_inside_legacy_input_wrap():
    cleaned = strip_tool_arguments(
        {
            "input": {
                "value": "ok",
                "_meta": {"userId": "eve"},
                f"{MCP_META_PREFIX}trace": {"id": "nested"},
            },
            "_meta": {"trace": {"id": "outer"}},
        }
    )
    assert cleaned == {"input": {"value": "ok"}}


def test_strip_leaves_nested_user_meta_values():
    cleaned = strip_tool_arguments({"payload": {"_meta": "user-owned"}})
    assert cleaned == {"payload": {"_meta": "user-owned"}}


def test_strip_empty_and_none():
    assert strip_tool_arguments(None) == {}
    assert strip_tool_arguments({}) == {}


def _echo_app(seen: dict, *, task_support: str = "optional"):
    @injectable()
    class EchoController:
        @tool(
            name="echo",
            description="echo",
            input_schema=LooseInput,
            task_support=task_support,
        )
        async def echo(self, input: LooseInput, context: ExecutionContext) -> dict:
            seen["keys"] = set(input.model_dump().keys())
            seen["extra"] = dict(getattr(input, "model_extra", None) or {})
            seen["value"] = input.value
            seen["user"] = context.user
            seen["trace"] = context.rpc_meta.trace if context.rpc_meta else None
            return {"value": input.value, "keys": sorted(seen["keys"])}

    @module(name="StripToolArgs", controllers=[EchoController])
    class EchoModule:
        pass

    @mcp_app(module=EchoModule, server=ServerConfig(name="strip-tool-args"))
    class EchoApp:
        pass

    return asyncio.run(McpApplicationFactory.create(EchoApp))


def test_handler_input_never_contains_meta():
    seen: dict = {}
    app = _echo_app(seen)
    result = asyncio.run(
        app._call_tool(
            "echo",
            {
                "value": "ok",
                "_meta": {"userId": "eve", "trace": {"id": "from-args"}},
                f"{MCP_META_PREFIX}auth": {"userId": "mallory"},
            },
        )
    )
    assert result.is_error is not True
    assert seen["value"] == "ok"
    assert "_meta" not in seen["keys"]
    assert "_meta" not in seen["extra"]
    assert all(not str(key).startswith(MCP_META_PREFIX) for key in seen["keys"])
    assert all(not str(key).startswith(MCP_META_PREFIX) for key in seen["extra"])


def test_task_path_strips_arguments_before_handler():
    seen: dict = {}
    app = _echo_app(seen, task_support="optional")
    created = asyncio.run(
        app._call_tool(
            "echo",
            {
                "value": "task-ok",
                "_meta": {"userId": "eve"},
                f"{MCP_META_PREFIX}protocolVersion": "2026-07-28",
            },
            task=types.TaskMetadata(ttl=60_000),
        )
    )
    task_id = created.task.task_id
    finished = asyncio.run(app.task_manager.wait_until_done(task_id))
    assert finished.status == TaskStatus.COMPLETED
    assert seen["value"] == "task-ok"
    assert "_meta" not in seen["keys"]
    assert "_meta" not in seen["extra"]


def test_http_strips_argument_meta_and_keeps_envelope_trace(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    seen: dict = {}
    app = _echo_app(seen)
    http_app = app.get_combined_app(json_response=True)
    try:
        with TestClient(http_app) as client:
            response = client.post(
                "/mcp",
                headers=CALL_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "echo",
                        "arguments": {
                            "value": "ok",
                            "_meta": {"userId": "eve", "trace": {"id": "from-args"}},
                            f"{MCP_META_PREFIX}auth": {"token": "secret"},
                        },
                        "_meta": {"trace": {"id": "from-envelope"}},
                    },
                },
            )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        assert body["value"] == "ok"
        assert "_meta" not in body["keys"]
        assert seen["trace"] == {"id": "from-envelope"}
        assert seen["user"] is None
        assert "_meta" not in seen["extra"]
    finally:
        DIContainer.reset()


def test_http_jwt_still_comes_from_header_not_argument_meta(monkeypatch):
    monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
    monkeypatch.delenv("MCP_STATELESS", raising=False)
    seen: dict = {}
    jwt = JWTService()
    DIContainer.get_instance().register_value(JWTService, jwt)
    token = jwt.create_token({"sub": "alice", "tenant_id": "acme"})
    app = _echo_app(seen)
    http_app = app.get_combined_app(json_response=True)
    try:
        with TestClient(http_app) as client:
            response = client.post(
                "/mcp",
                headers={**CALL_HEADERS, "Authorization": f"Bearer {token}"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "echo",
                        "arguments": {
                            "value": "ok",
                            "_meta": {"userId": "eve", "authorization": "Bearer spoofed"},
                        },
                    },
                },
            )
        assert response.status_code == 200, response.text
        assert seen["user"] == "alice"
        assert "_meta" not in seen["keys"]
    finally:
        DIContainer.reset()
