"""Epic and acceptance-criteria verification for PYTHONSDK-11 (Doc 11)."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

import mcp.types as types
import pytest
from mcp.server.experimental.request_context import Experimental
from mcp.server.lowlevel.server import request_ctx, RequestContext
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.task import TaskManager, TaskStatus
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.runtime.conformance import assert_blueprint_layout
from nitrostack.runtime.epic_acceptance import (
    ACCEPTANCE_CRITERIA,
    EPIC_DELIVERABLES,
    MCP20_SPEC_DOCS,
    MCP20_TEST_MODULES,
    ImplementationEpic,
    acceptance_criteria_registered,
    deliverables_for_epic,
    epic_coverage_complete,
    iter_epic_summary,
    modern_protocol_target,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC_CANDIDATES = (
    REPO_ROOT.parent / "traker" / "stateless-doc-for-python",
    REPO_ROOT.parent.parent / "traker" / "stateless-doc-for-python",
)


def _resolve_spec_root() -> Path | None:
    for candidate in _SPEC_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


SPEC_ROOT = _resolve_spec_root()


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestEpicRegistry:
    def test_all_seven_epics_have_deliverables(self):
        assert epic_coverage_complete()
        for line in iter_epic_summary():
            assert "0 deliverable" not in line

    def test_epic_deliverable_count(self):
        assert len(EPIC_DELIVERABLES) >= 14

    def test_acceptance_criteria_registered(self):
        assert acceptance_criteria_registered()
        keys = {item.key for item in ACCEPTANCE_CRITERIA}
        assert keys == {
            "stateless",
            "task_conformance",
            "task_cancellation",
            "ttl_safety",
            "multi_tenant",
            "ssrf_security",
            "error_parity",
            "automated_tests",
        }

    def test_modern_protocol_target(self):
        assert modern_protocol_target() == "2026-07-28"

    @pytest.mark.parametrize("epic", list(ImplementationEpic))
    def test_each_epic_maps_to_spec_and_tests(self, epic: ImplementationEpic):
        items = deliverables_for_epic(epic)
        assert items, f"Epic {epic} has no deliverables"
        for item in items:
            assert item.spec_doc.endswith(".md")
            assert item.test_module.startswith("tests/test_mcp20_doc")


class TestSpecAndTestTraceability:
    def test_all_mcp20_test_modules_exist(self):
        for module_path in MCP20_TEST_MODULES:
            path = REPO_ROOT / module_path
            assert path.is_file(), module_path

    @pytest.mark.parametrize("doc_name", MCP20_SPEC_DOCS)
    def test_spec_documents_exist(self, doc_name: str):
        if SPEC_ROOT is None:
            pytest.skip("Spec folder not available beside the SDK checkout")
        path = SPEC_ROOT / doc_name
        assert path.is_file(), path
        assert path.stat().st_size > 0

    def test_blueprint_layout_importable(self):
        assert_blueprint_layout()


class TestAcceptanceTaskConformance:
    def test_task_augmented_call_returns_immediately(self):
        @injectable()
        class AsyncController:
            @tool(name="fast_task", description="fast", input_schema=EchoInput, task_support="optional")
            async def fast_task(self, input: EchoInput, context: ExecutionContext) -> str:
                await asyncio.sleep(0.05)
                return input.value

        @module(name="Doc11Tasks", controllers=[AsyncController])
        class TasksModule:
            pass

        @mcp_app(module=TasksModule, server=ServerConfig(name="doc11-tasks"))
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
                @tool(name="noop_doc11", description="noop", input_schema=EchoInput)
                async def noop_doc11(self, input: EchoInput, context: ExecutionContext) -> str:
                    return "ok"

            @module(name="Doc11Cancel", controllers=[DummyController])
            class CancelModule:
                pass

            @mcp_app(module=CancelModule, server=ServerConfig(name="doc11-cancel"))
            class CancelApp:
                pass

            from mcp.shared.exceptions import McpError

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


class TestAutomatedTestCoverageMap:
    def test_doc_test_modules_importable(self):
        for module_path in MCP20_TEST_MODULES:
            file_path = REPO_ROOT / module_path
            spec = importlib.util.spec_from_file_location(
                module_path.replace("/", "_").replace(".", "_"),
                file_path,
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

    def test_full_mcp20_suite_module_count(self):
        doc_tests = sorted(REPO_ROOT.glob("tests/test_mcp20_doc*.py"))
        assert len(doc_tests) == 12
