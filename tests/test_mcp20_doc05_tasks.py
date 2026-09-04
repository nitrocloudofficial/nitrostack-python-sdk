"""Tests for MCP 2.0 Tasks protocol and lifecycle (Doc 05)."""

import asyncio
import os
import sys

import mcp.types as types
from mcp.shared.exceptions import McpError
import pytest
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import TaskAlreadyTerminalError
from nitrostack.core.task import TaskManager, TaskStatus
from nitrostack.protocol.tasks import (
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_TASK_TTL_MS,
    RESULT_TYPE_TASK,
    build_task_create_jsonrpc_result,
    ttl_ms_to_seconds,
)
from mcp.server.lowlevel.server import request_ctx, RequestContext
from mcp.server.experimental.request_context import Experimental


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestTaskProtocolHelpers:
    def test_ttl_ms_to_seconds(self):
        assert ttl_ms_to_seconds(600_000) == 600
        assert ttl_ms_to_seconds(None) is None

    def test_task_create_envelope(self):
        envelope = build_task_create_jsonrpc_result(
            "req-1",
            {"taskId": "t1", "status": "working"},
        )
        assert envelope["result"]["resultType"] == RESULT_TYPE_TASK
        assert envelope["result"]["task"]["taskId"] == "t1"


class TestTaskManagerLifecycle:
    def test_input_required_transition(self):
        manager = TaskManager()
        task = manager.create_task()
        manager.require_input(task.id, {"resultType": "input_required", "inputRequests": []})
        snapshot = manager.get_task(task.id)
        assert snapshot.status == TaskStatus.INPUT_REQUIRED
        assert snapshot.result["resultType"] == "input_required"

    def test_resume_from_input_required(self):
        manager = TaskManager()
        task = manager.create_task()
        manager.require_input(task.id, {"pause": True})
        manager.resume_task(task.id)
        assert manager.get_task(task.id).status == TaskStatus.WORKING

    def test_create_task_stores_ttl_ms(self):
        task = TaskManager().create_task(ttl_ms=600_000)
        assert task.ttl_ms == 600_000
        assert task.ttl == 600_000
        assert task.poll_interval == DEFAULT_POLL_INTERVAL_MS


class TestTaskSupportNegotiation:
    def test_forbidden_tool_rejects_task_augmentation(self):
        @injectable()
        class SyncController:
            @tool(name="sync_only", description="sync", input_schema=EchoInput, task_support="forbidden")
            async def sync_only(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="TasksDoc05Forbidden", controllers=[SyncController])
        class ForbiddenModule:
            pass

        @mcp_app(module=ForbiddenModule, server=ServerConfig(name="tasks-forbidden"))
        class ForbiddenApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(ForbiddenApp)
            token = request_ctx.set(
                RequestContext(
                    request_id="1",
                    meta=None,
                    session=None,
                    lifespan_context=None,
                    experimental=Experimental(task_metadata=types.TaskMetadata(ttl=60_000)),
                )
            )
            try:
                with pytest.raises(McpError) as exc:
                    await app._call_tool("sync_only", {"value": "x"})
                assert exc.value.error.code == types.METHOD_NOT_FOUND
            finally:
                request_ctx.reset(token)

        asyncio.run(_run())

    def test_required_tool_rejects_sync_call(self):
        @injectable()
        class HeavyController:
            @tool(name="heavy_job", description="heavy", input_schema=EchoInput, task_support="required")
            async def heavy_job(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="TasksDoc05Required", controllers=[HeavyController])
        class RequiredModule:
            pass

        @mcp_app(module=RequiredModule, server=ServerConfig(name="tasks-required"))
        class RequiredApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(RequiredApp)
            with pytest.raises(McpError) as exc:
                await app._call_tool("heavy_job", {"value": "x"})
            assert exc.value.error.code == types.INVALID_REQUEST

        asyncio.run(_run())


class TestTaskWireHandlers:
    def test_tasks_get_embeds_completed_result(self):
        manager = TaskManager()
        task = manager.create_task(ttl_ms=DEFAULT_TASK_TTL_MS)
        manager.complete_task(task.id, types.CallToolResult(content=[types.TextContent(type="text", text="done")]))

        @injectable()
        class DummyController:
            @tool(name="noop", description="noop", input_schema=EchoInput)
            async def noop(self, input: EchoInput, context: ExecutionContext) -> str:
                return "ok"

        @module(name="TasksDoc05Get", controllers=[DummyController])
        class GetModule:
            pass

        @mcp_app(module=GetModule, server=ServerConfig(name="tasks-get"))
        class GetApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(GetApp)
            app.task_manager = manager
            handler = app.mcp_server.request_handlers[types.GetTaskRequest]
            response = await handler(
                types.GetTaskRequest(
                    method="tasks/get",
                    params=types.GetTaskRequestParams(taskId=task.id),
                )
            )
            payload = response.model_dump(by_alias=True)
            assert payload["status"] == "completed"
            assert payload["result"]["content"][0]["text"] == "done"

        asyncio.run(_run())

    def test_tasks_list_deprecated_on_modern_protocol(self):
        @injectable()
        class DummyController:
            @tool(name="noop2", description="noop", input_schema=EchoInput)
            async def noop2(self, input: EchoInput, context: ExecutionContext) -> str:
                return "ok"

        @module(name="TasksDoc05List", controllers=[DummyController])
        class ListModule:
            pass

        @mcp_app(module=ListModule, server=ServerConfig(name="tasks-list"))
        class ListApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(ListApp)
            handler = app.mcp_server.request_handlers[types.ListTasksRequest]
            with pytest.raises(McpError) as exc:
                await handler(types.ListTasksRequest(method="tasks/list", params={}))
            assert exc.value.error.code == types.METHOD_NOT_FOUND

        asyncio.run(_run())

    def test_cancel_terminal_task_raises_invalid_params(self):
        manager = TaskManager()
        task = manager.create_task()
        manager.complete_task(task.id, {"ok": True})

        @injectable()
        class DummyController:
            @tool(name="noop3", description="noop", input_schema=EchoInput)
            async def noop3(self, input: EchoInput, context: ExecutionContext) -> str:
                return "ok"

        @module(name="TasksDoc05Cancel", controllers=[DummyController])
        class CancelModule:
            pass

        @mcp_app(module=CancelModule, server=ServerConfig(name="tasks-cancel"))
        class CancelApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(CancelApp)
            app.task_manager = manager
            handler = app.mcp_server.request_handlers[types.CancelTaskRequest]
            with pytest.raises(McpError) as exc:
                await handler(
                    types.CancelTaskRequest(
                        method="tasks/cancel",
                        params=types.CancelTaskRequestParams(taskId=task.id),
                    )
                )
            assert exc.value.error.code == types.INVALID_PARAMS
            assert "terminal" in exc.value.error.message.lower()

        asyncio.run(_run())

    def test_task_augmented_call_returns_task_handle(self):
        @injectable()
        class AsyncController:
            @tool(name="slow_echo", description="slow", input_schema=EchoInput, task_support="optional")
            async def slow_echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="TasksDoc05Create", controllers=[AsyncController])
        class CreateModule:
            pass

        @mcp_app(module=CreateModule, server=ServerConfig(name="tasks-create"))
        class CreateApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(CreateApp)
            token = request_ctx.set(
                RequestContext(
                    request_id="1",
                    meta=None,
                    session=None,
                    lifespan_context=None,
                    experimental=Experimental(task_metadata=types.TaskMetadata(ttl=120_000)),
                )
            )
            try:
                result = await app._call_tool("slow_echo", {"value": "async"})
                assert isinstance(result, types.CreateTaskResult)
                assert result.task.status == "working"
                assert result.task.ttl == 120_000
            finally:
                request_ctx.reset(token)

        asyncio.run(_run())
