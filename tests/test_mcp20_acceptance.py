"""Acceptance-criteria verification for MCP 2026-07-28."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import mcp.types as types
import pytest
from nitrostack.runtime.request_ctx import Experimental, RequestContext, RequestParamsMeta, request_ctx
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.task import TaskManager, TaskStatus
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION



class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestAcceptanceTaskConformance:
    def test_task_augmented_call_returns_immediately(self):
        @injectable()
        class AsyncController:
            @tool(name="fast_task", description="fast", input_schema=EchoInput, task_support="optional")
            async def fast_task(self, input: EchoInput, context: ExecutionContext) -> str:
                await asyncio.sleep(0.05)
                return input.value

        @module(name="AcceptanceTasks", controllers=[AsyncController])
        class TasksModule:
            pass

        @mcp_app(module=TasksModule, server=ServerConfig(name="acceptance-tasks", protocol_era="legacy"))
        class TasksApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(TasksApp)
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
                started = time.perf_counter()
                result = await app._call_tool("fast_task", {"value": "x"})
                elapsed_ms = (time.perf_counter() - started) * 1000
                assert isinstance(result, types.CreateTaskResult)
                assert result.task.status == "working"
                assert elapsed_ms < 500, f"task handle took {elapsed_ms:.1f}ms"
            finally:
                request_ctx.reset(token)

        asyncio.run(_run())

    def test_cancel_on_terminal_task_returns_invalid_params(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(tool_name="job")
            await manager.complete_task(task.id, {"ok": True})

            @injectable()
            class DummyController:
                @tool(name="noop_accept", description="noop", input_schema=EchoInput)
                async def noop_accept(self, input: EchoInput, context: ExecutionContext) -> str:
                    return "ok"

            @module(name="AcceptanceCancel", controllers=[DummyController])
            class CancelModule:
                pass

            @mcp_app(module=CancelModule, server=ServerConfig(name="acceptance-cancel", protocol_era="legacy"))
            class CancelApp:
                pass

            from mcp import MCPError as McpError

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

        asyncio.run(_run())

    def test_active_task_not_evicted_before_terminal(self):
        from nitrostack.tasks.eviction import should_evict_terminal_task
        from nitrostack.tasks.types import TaskEntry, TaskWireData, datetime_to_ms, utc_now

        now = utc_now()
        entry = TaskEntry(
            task_id="active",
            data=TaskWireData(
                task_id="active",
                status="working",
                created_at=now,
                last_updated_at=now,
                ttl_ms=1,
            ),
            status="working",
        )
        assert should_evict_terminal_task(entry, datetime_to_ms(now) + 10_000) is False

    def test_completed_task_snapshot_status(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(tool_name="job")
            await manager.cancel_task(task.id)
            snapshot = await manager.get_task(task.id)
            assert snapshot.status == TaskStatus.CANCELLED

        asyncio.run(_run())


class TestAcceptanceStatelessInvariant:
    def test_legacy_session_header_constant(self):
        assert LEGACY_SESSION_HEADER == "Mcp-Session-Id"

    def test_server_config_defaults_to_modern_protocol(self):
        cfg = ServerConfig(name="x")
        assert cfg.protocol_version == MODERN_PROTOCOL_VERSION

