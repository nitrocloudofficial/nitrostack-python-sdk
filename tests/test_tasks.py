"""
Phase 1 task management tests.

Covers TaskManager state machine unit tests (25+) and MCP task integration.
"""

import asyncio
import datetime
import os
import sys
import time

import pytest
from pydantic import BaseModel

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import module, injectable, tool, ExecutionContext, NitroTestingModule
from nitrostack.core.context import TaskContext
from nitrostack.core.errors import (
    InvalidTaskTransitionError,
    TaskAlreadyTerminalError,
    TaskCancelledError,
    TaskExpiredError,
    TaskNotFoundError,
)
from nitrostack.core.task import (
    TERMINAL_STATUSES,
    TaskData,
    TaskManager,
    TaskStatus,
    is_terminal_status,
)
import mcp.types as types
from mcp.server.lowlevel.server import request_ctx, RequestContext
from mcp.server.experimental.request_context import Experimental


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manager() -> TaskManager:
    return TaskManager()


def _force_expire(manager: TaskManager, task_id: str) -> None:
    """Set expires_at in the past so the next access lazily expires the task."""
    entry = manager._tasks[task_id]
    entry.data.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=1
    )


# ===========================================================================
# is_terminal_status / TERMINAL_STATUSES
# ===========================================================================

class TestTerminalHelpers:
    def test_terminal_statuses_contains_expected_values(self):
        assert TERMINAL_STATUSES == {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.EXPIRED,
        }

    def test_is_terminal_true_for_terminal_statuses(self):
        for status in TERMINAL_STATUSES:
            assert is_terminal_status(status) is True

    def test_is_terminal_false_for_working(self):
        assert is_terminal_status(TaskStatus.WORKING) is False


# ===========================================================================
# create_task
# ===========================================================================

class TestCreateTask:
    def test_creates_task_with_working_status(self):
        task = _manager().create_task()
        assert task.status == TaskStatus.WORKING
        assert task.id
        assert task.created_at is not None
        assert task.progress == "Task created"

    def test_default_ttl_is_none_never_expires(self):
        task = _manager().create_task()
        assert task.ttl_seconds is None
        assert task.expires_at is None

    def test_custom_ttl_sets_expires_at(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        task = _manager().create_task(ttl_seconds=60)
        after = datetime.datetime.now(datetime.timezone.utc)
        assert task.ttl_seconds == 60
        assert task.expires_at is not None
        assert before <= task.expires_at - datetime.timedelta(seconds=60) <= after

    def test_generates_unique_task_ids(self):
        manager = _manager()
        a = manager.create_task()
        b = manager.create_task()
        assert a.id != b.id

    def test_returns_snapshot_not_live_reference(self):
        manager = _manager()
        task = manager.create_task()
        snapshot_progress = task.progress
        manager.update_progress(task.id, "changed")
        assert task.progress == snapshot_progress
        assert manager.get_task(task.id).progress == "changed"

    def test_concurrent_task_creation_no_id_collisions(self):
        manager = _manager()
        ids = [manager.create_task().id for _ in range(100)]
        assert len(ids) == len(set(ids))


# ===========================================================================
# get_task
# ===========================================================================

class TestGetTask:
    def test_returns_existing_task(self):
        manager = _manager()
        created = manager.create_task()
        fetched = manager.get_task(created.id)
        assert fetched.id == created.id
        assert fetched.status == TaskStatus.WORKING

    def test_unknown_task_raises_not_found(self):
        with pytest.raises(TaskNotFoundError) as exc:
            _manager().get_task("missing-id")
        assert exc.value.task_id == "missing-id"

    def test_has_task(self):
        manager = _manager()
        task = manager.create_task()
        assert manager.has_task(task.id) is True
        assert manager.has_task("nope") is False


# ===========================================================================
# Progress / transitions / terminal states
# ===========================================================================

class TestProgressAndTransitions:
    def test_update_progress_on_working_task(self):
        manager = _manager()
        task = manager.create_task()
        manager.update_progress(task.id, "step 1")
        assert manager.get_task(task.id).progress == "step 1"
        assert manager.get_task(task.id).status == TaskStatus.WORKING

    def test_complete_from_working(self):
        manager = _manager()
        task = manager.create_task()
        manager.complete_task(task.id, {"ok": True})
        data = manager.get_task(task.id)
        assert data.status == TaskStatus.COMPLETED
        assert data.result == {"ok": True}
        assert is_terminal_status(data.status)

    def test_fail_from_working(self):
        manager = _manager()
        task = manager.create_task()
        manager.fail_task(task.id, RuntimeError("boom"))
        data = manager.get_task(task.id)
        assert data.status == TaskStatus.FAILED
        assert "boom" in str(data.error)

    def test_cancel_from_working(self):
        manager = _manager()
        task = manager.create_task()
        manager.cancel_task(task.id)
        data = manager.get_task(task.id)
        assert data.status == TaskStatus.CANCELLED

    def test_complete_after_cancel_raises(self):
        manager = _manager()
        task = manager.create_task()
        manager.cancel_task(task.id)
        with pytest.raises(TaskAlreadyTerminalError):
            manager.complete_task(task.id, "late")

    def test_fail_after_complete_raises(self):
        manager = _manager()
        task = manager.create_task()
        manager.complete_task(task.id, "done")
        with pytest.raises(TaskAlreadyTerminalError):
            manager.fail_task(task.id, "late")

    def test_cancel_after_complete_raises_already_terminal(self):
        manager = _manager()
        task = manager.create_task()
        manager.complete_task(task.id, "done")
        with pytest.raises(TaskAlreadyTerminalError):
            manager.cancel_task(task.id)

    def test_update_progress_on_terminal_raises(self):
        manager = _manager()
        task = manager.create_task()
        manager.complete_task(task.id, "done")
        with pytest.raises(TaskAlreadyTerminalError):
            manager.update_progress(task.id, "too late")

    def test_happy_path_create_progress_complete_get_result(self):
        manager = _manager()
        task = manager.create_task()
        manager.update_progress(task.id, "halfway")
        manager.complete_task(task.id, "final-result")
        assert manager.get_task(task.id).result == "final-result"
        assert manager.get_result(task.id) == "final-result"


# ===========================================================================
# Result retrieval / listing
# ===========================================================================

class TestResultAndList:
    def test_get_result_on_non_completed_raises(self):
        manager = _manager()
        task = manager.create_task()
        with pytest.raises(InvalidTaskTransitionError):
            manager.get_result(task.id)

    def test_list_tasks_includes_created(self):
        manager = _manager()
        a = manager.create_task()
        b = manager.create_task()
        ids = {t.id for t in manager.list_tasks()}
        assert a.id in ids and b.id in ids

    def test_list_tasks_empty(self):
        assert _manager().list_tasks() == []

    def test_wait_until_done_returns_completed(self):
        async def _run():
            manager = _manager()
            task = manager.create_task()

            async def finish():
                await asyncio.sleep(0.05)
                manager.complete_task(task.id, "async-ok")

            asyncio.create_task(finish())
            done = await manager.wait_until_done(task.id)
            assert done.status == TaskStatus.COMPLETED
            assert done.result == "async-ok"

        asyncio.run(_run())


# ===========================================================================
# TTL / expiration
# ===========================================================================

class TestTTLExpiration:
    def test_task_without_ttl_never_expires(self):
        manager = _manager()
        task = manager.create_task()
        # Even if we wait a bit, no expires_at means still WORKING
        time.sleep(0.01)
        data = manager.get_task(task.id)
        assert data.status == TaskStatus.WORKING
        assert data.expires_at is None

    def test_expired_task_transitions_to_expired_on_read(self):
        manager = _manager()
        task = manager.create_task(ttl_seconds=1)
        _force_expire(manager, task.id)
        data = manager.get_task(task.id)
        assert data.status == TaskStatus.EXPIRED
        assert is_terminal_status(data.status)

    def test_update_progress_on_expired_raises_task_expired(self):
        manager = _manager()
        task = manager.create_task(ttl_seconds=1)
        _force_expire(manager, task.id)
        with pytest.raises(TaskExpiredError):
            manager.update_progress(task.id, "nope")

    def test_complete_on_expired_raises_task_expired(self):
        manager = _manager()
        task = manager.create_task(ttl_seconds=1)
        _force_expire(manager, task.id)
        with pytest.raises(TaskExpiredError):
            manager.complete_task(task.id, "late")

    def test_cancel_on_expired_raises_task_expired(self):
        manager = _manager()
        task = manager.create_task(ttl_seconds=1)
        _force_expire(manager, task.id)
        with pytest.raises(TaskExpiredError):
            manager.cancel_task(task.id)

    def test_get_result_on_expired_raises(self):
        manager = _manager()
        task = manager.create_task(ttl_seconds=1)
        _force_expire(manager, task.id)
        with pytest.raises(TaskExpiredError):
            manager.get_result(task.id)


# ===========================================================================
# TaskContext
# ===========================================================================

class TestTaskContext:
    def test_update_progress_delegates_to_manager(self):
        manager = _manager()
        task = manager.create_task()
        ctx = TaskContext(task.id, manager)
        ctx.update_progress("via context")
        assert ctx.progress_message == "via context"
        assert manager.get_task(task.id).progress == "via context"

    def test_cancel_sets_cancelled(self):
        manager = _manager()
        task = manager.create_task()
        ctx = TaskContext(task.id, manager)
        ctx.cancel()
        assert ctx.is_cancelled is True
        assert manager.get_task(task.id).status == TaskStatus.CANCELLED

    def test_throw_if_cancelled_raises(self):
        manager = _manager()
        task = manager.create_task()
        ctx = TaskContext(task.id, manager)
        manager.cancel_task(task.id)
        with pytest.raises(TaskCancelledError):
            ctx.throw_if_cancelled()

    def test_throw_if_cancelled_noop_when_working(self):
        manager = _manager()
        task = manager.create_task()
        ctx = TaskContext(task.id, manager)
        ctx.throw_if_cancelled()  # should not raise

    def test_update_progress_after_cancel_does_not_raise(self):
        manager = _manager()
        task = manager.create_task()
        ctx = TaskContext(task.id, manager)
        manager.cancel_task(task.id)
        ctx.update_progress("ignored")  # swallowed


# ===========================================================================
# Exception classes
# ===========================================================================

class TestTaskExceptions:
    def test_task_not_found_error_message(self):
        err = TaskNotFoundError("abc")
        assert err.task_id == "abc"
        assert "abc" in str(err)

    def test_task_already_terminal_error(self):
        err = TaskAlreadyTerminalError("t1", TaskStatus.COMPLETED)
        assert err.task_id == "t1"
        assert "completed" in str(err)

    def test_invalid_task_transition_error(self):
        err = InvalidTaskTransitionError(TaskStatus.WORKING, TaskStatus.EXPIRED)
        assert "working" in str(err) and "expired" in str(err)

    def test_task_expired_error(self):
        err = TaskExpiredError("t2")
        assert err.task_id == "t2"

    def test_task_cancelled_error(self):
        err = TaskCancelledError("t3")
        assert err.task_id == "t3"
        assert "cancelled" in str(err).lower()

    def test_task_data_dataclass_fields(self):
        data = TaskData(id="x", status=TaskStatus.WORKING, progress="p")
        assert data.id == "x"
        assert data.result is None
        assert data.expires_at is None

    def test_context_task_cancelled_error_import_path(self):
        from nitrostack.core.context import TaskCancelledError as FromContext
        from nitrostack.core.errors import TaskCancelledError as FromErrors

        assert FromContext is FromErrors


# ===========================================================================
# MCP integration (Phase 0 architecture regression)
# ===========================================================================

class AsyncTaskInput(BaseModel):
    duration: float


@injectable(deps=[])
class AsyncTaskController:
    @tool(
        name="delayed_tool",
        description="Simulate a long running task",
        input_schema=AsyncTaskInput,
        task_support="optional",
    )
    async def delayed_tool(self, input: AsyncTaskInput, context: ExecutionContext) -> str:
        if context.task:
            context.task.update_progress("Task started...")
            await asyncio.sleep(input.duration)
            context.task.throw_if_cancelled()
            context.task.update_progress("Finishing...")
            return "Success payload!"
        return "Sync return!"


@module(
    name="test_tasks",
    controllers=[AsyncTaskController],
    providers=[],
    imports=[],
    exports=[],
)
class TestTasksModule:
    pass


async def _mcp_task_flow():
    harness = await NitroTestingModule.create(TestTasksModule)

    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="delayed_tool",
            arguments={"input": {"duration": 0.2}},
            task=types.TaskMetadata(ttl=60_000),
        ),
    )

    handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
    token = request_ctx.set(
        RequestContext(
            request_id="test-req-1",
            meta=None,
            session=None,
            lifespan_context=None,
            experimental=Experimental(task_metadata=req.params.task),
            request=req,
        )
    )
    try:
        response = await handler(req)
    finally:
        request_ctx.reset(token)

    assert isinstance(response.root, types.CreateTaskResult)
    task_id = response.root.task.taskId

    get_req = types.GetTaskRequest(
        method="tasks/get",
        params=types.GetTaskRequestParams(taskId=task_id),
    )
    get_handler = harness.app.mcp_server.request_handlers[types.GetTaskRequest]
    get_res = await get_handler(get_req)
    assert get_res.status == "working"

    deadline = time.time() + 5
    get_res2 = get_res
    while get_res2.status not in ("completed", "failed", "cancelled") and time.time() < deadline:
        await asyncio.sleep(0.05)
        get_res2 = await get_handler(get_req)

    assert get_res2.status == "completed"
    assert get_res2.result is not None
    assert "Success payload!" in get_res2.result["content"][0]["text"]

    # Cancellation path
    req_cancel = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="delayed_tool",
            arguments={"input": {"duration": 1.0}},
            task=types.TaskMetadata(ttl=60_000),
        ),
    )
    token = request_ctx.set(
        RequestContext(
            request_id="test-req-2",
            meta=None,
            session=None,
            lifespan_context=None,
            experimental=Experimental(task_metadata=req_cancel.params.task),
            request=req_cancel,
        )
    )
    try:
        resp_cancel = await handler(req_cancel)
    finally:
        request_ctx.reset(token)

    task_id_cancel = resp_cancel.root.task.taskId
    cancel_req = types.CancelTaskRequest(
        method="tasks/cancel",
        params=types.CancelTaskRequestParams(taskId=task_id_cancel),
    )
    cancel_handler = harness.app.mcp_server.request_handlers[types.CancelTaskRequest]
    cancel_res = await cancel_handler(cancel_req)
    assert cancel_res.status == "cancelled"

    get_res_cancel = await get_handler(
        types.GetTaskRequest(
            method="tasks/get",
            params=types.GetTaskRequestParams(taskId=task_id_cancel),
        )
    )
    assert get_res_cancel.status == "cancelled"


def test_mcp_task_integration():
    asyncio.run(_mcp_task_flow())


def test_all_tasks():
    """Backward-compatible entrypoint used by README / prior runners."""
    asyncio.run(_mcp_task_flow())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
