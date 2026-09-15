"""Issue #25: the partial Tasks surface is hidden on the 2026 wire."""

import asyncio
import json

import httpx
import mcp.types as types
import pytest
from mcp import MCPError
from pydantic import BaseModel

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import TaskNotFoundError


class TaskInput(BaseModel):
    value: str = ""


@injectable()
class HideTasksController:
    @tool(
        name="taskable",
        description="taskable",
        input_schema=TaskInput,
        task_support="optional",
    )
    async def taskable(self, input: TaskInput, context: ExecutionContext) -> str:
        return "ok"


@module(name="hide_tasks", controllers=[HideTasksController])
class HideTasksModule:
    pass


def _app(era: str):
    @mcp_app(module=HideTasksModule, server=ServerConfig(name="hide-tasks", protocol_era=era))
    class App:
        pass

    return asyncio.run(McpApplicationFactory.create(App))


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_2026_capabilities_and_handlers_hide_tasks(era):
    app = _app(era)
    server = app.mcp_server
    assert server is not None
    assert server.has_task_support is False
    assert server.get_capabilities(protocol_version="2026-07-28").tasks is None
    assert types.GetTaskRequest not in server.request_handlers
    assert types.CancelTaskRequest not in server.request_handlers
    assert types.GetTaskPayloadRequest not in server.request_handlers


def test_legacy_keeps_the_existing_in_process_task_handlers():
    app = _app("legacy")
    server = app.mcp_server
    assert server is not None
    assert server.has_task_support is True
    assert types.GetTaskRequest in server.request_handlers


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_task_augmented_tool_call_is_rejected_without_creating_a_task(era):
    app = _app(era)

    async def run():
        with pytest.raises(MCPError) as exc_info:
            await app._call_tool(
                "taskable",
                {},
                task=types.TaskMetadata(ttl=60_000),
            )
        assert getattr(exc_info.value, "error", None).code == types.INVALID_PARAMS
        with pytest.raises(TaskNotFoundError):
            await app.task_manager.get_task("task_does_not_exist")

    asyncio.run(run())


def _http_request(app, payload: dict, *, extra_headers: dict[str, str] | None = None):
    async def run():
        http_app = app.get_combined_app(json_response=True)
        transport = httpx.ASGITransport(app=http_app)
        async with http_app.router.lifespan_context(http_app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                headers = {
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                }
                headers.update(extra_headers or {})
                return await client.post(
                    "/mcp",
                    headers=headers,
                    content=json.dumps(payload),
                )

    return asyncio.run(run())


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_2026_discover_does_not_advertise_tasks(era):
    response = _http_request(
        _app(era),
        {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "server/discover",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
},
        extra_headers={
            "Mcp-Method": "server/discover",
            "MCP-Protocol-Version": "2026-07-28",
        },
    )
    assert response.status_code == 200
    body = response.json()
    if "error" in body:
        # Modern discovery is optional in auto; when present it must still
        # agree with the low-level capabilities and never advertise Tasks.
        assert body["error"]["code"] == -32601
        return
    capabilities = body["result"].get("capabilities", {})
    assert "tasks" not in capabilities
    assert "io.modelcontextprotocol/tasks" not in capabilities.get("extensions", {})


@pytest.mark.parametrize("method", ["tasks/get", "tasks/list", "tasks/result", "tasks/cancel"])
def test_2026_task_methods_are_method_not_found(method):
    response = _http_request(
        _app("auto"),
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601


def test_2026_task_augmented_http_call_is_not_a_task_result():
    response = _http_request(
        _app("auto"),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "taskable",
                "arguments": {},
                "task": {"ttl": 60_000},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32602
    assert "resultType" not in body.get("result", {})
