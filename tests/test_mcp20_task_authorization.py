"""Tests for MCP 2.0 task authorization and tenant isolation."""

import asyncio
import os
import sys

import mcp.types as types
import pytest
from mcp.shared.exceptions import McpError
from mcp.server.experimental.request_context import Experimental
from mcp.server.lowlevel.server import request_ctx, RequestContext
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import TaskNotFoundError
from nitrostack.core.task import TaskManager
from nitrostack.tasks.authorization import (
    check_task_access,
    entry_matches_access_context,
    extract_task_access_context,
    list_task_wire_data_for_context,
)
from nitrostack.tasks.types import TaskAccessContext, TaskEntry, TaskWireData, utc_now


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestCheckTaskAccess:
    def test_allows_matching_tenant_and_user(self):
        entry = TaskEntry(
            task_id="t1",
            data=TaskWireData(task_id="t1"),
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        ctx = TaskAccessContext(user_id="user-a", tenant_id="tenant-a")
        check_task_access(entry, ctx)

    def test_denies_cross_tenant_with_not_found(self):
        entry = TaskEntry(
            task_id="t1",
            data=TaskWireData(task_id="t1"),
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        ctx = TaskAccessContext(user_id="user-a", tenant_id="tenant-b")
        with pytest.raises(TaskNotFoundError) as exc:
            check_task_access(entry, ctx)
        assert exc.value.task_id == "t1"

    def test_denies_cross_user_with_not_found(self):
        entry = TaskEntry(
            task_id="t1",
            data=TaskWireData(task_id="t1"),
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        ctx = TaskAccessContext(user_id="user-b", tenant_id="tenant-a")
        with pytest.raises(TaskNotFoundError):
            check_task_access(entry, ctx)

    def test_none_context_allows_internal_access(self):
        entry = TaskEntry(task_id="t1", data=TaskWireData(task_id="t1"), owner_id="user-a")
        check_task_access(entry, None)

    def test_empty_http_context_denies_owned_task(self):
        entry = TaskEntry(
            task_id="t1",
            data=TaskWireData(task_id="t1"),
            owner_id="user-a",
            tenant_id="tenant-a",
        )
        with pytest.raises(TaskNotFoundError):
            check_task_access(entry, TaskAccessContext())

    def test_partial_context_denies_when_task_has_tenant(self):
        entry = TaskEntry(
            task_id="t1",
            data=TaskWireData(task_id="t1"),
            owner_id="alice",
            tenant_id="acme",
        )
        with pytest.raises(TaskNotFoundError):
            check_task_access(entry, TaskAccessContext(user_id="alice"))


class TestListFiltering:
    def test_filters_by_tenant_and_sorts_desc(self):
        import datetime

        now = utc_now()
        older = now - datetime.timedelta(seconds=10)
        entries = [
            TaskEntry(
                task_id="older",
                data=TaskWireData(task_id="older", created_at=older, last_updated_at=older),
                tenant_id="tenant-a",
            ),
            TaskEntry(
                task_id="newer",
                data=TaskWireData(task_id="newer", created_at=now, last_updated_at=now),
                tenant_id="tenant-a",
            ),
            TaskEntry(
                task_id="other-tenant",
                data=TaskWireData(task_id="other-tenant", created_at=now, last_updated_at=now),
                tenant_id="tenant-b",
            ),
        ]
        ctx = TaskAccessContext(tenant_id="tenant-a")
        page, next_cursor = list_task_wire_data_for_context(entries, ctx, limit=10)
        assert [item.task_id for item in page] == ["newer", "older"]
        assert next_cursor is None
        assert entry_matches_access_context(entries[2], ctx) is False


class TestTaskManagerAuthorization:
    def test_get_task_enforces_access_context(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(
                ttl_ms=60_000,
                owner_id="alice",
                tenant_id="acme",
            )
            await manager.get_task(task.id, access_context=TaskAccessContext(user_id="alice", tenant_id="acme"))
            with pytest.raises(TaskNotFoundError):
                await manager.get_task(task.id, access_context=TaskAccessContext(user_id="bob", tenant_id="acme"))

        asyncio.run(_run())

    def test_cancel_cross_tenant_returns_not_found(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(owner_id="alice", tenant_id="acme")
            with pytest.raises(TaskNotFoundError):
                await manager.cancel_task(
                    task.id,
                    access_context=TaskAccessContext(user_id="alice", tenant_id="evil"),
                )

        asyncio.run(_run())


class TestWireHandlersAntiEnumeration:
    def test_tasks_get_returns_identical_error_for_missing_and_forbidden(self):
        @injectable()
        class OwnerController:
            @tool(name="owner_tool", description="owner", input_schema=EchoInput, task_support="optional")
            async def owner_tool(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="TaskAuth", controllers=[OwnerController])
        class AuthModule:
            pass

        @mcp_app(module=AuthModule, server=ServerConfig(name="task-auth"))
        class AuthApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(AuthApp)
            task = await app.task_manager.create_task(
                owner_id="alice",
                tenant_id="acme",
                ttl_ms=60_000,
            )
            handler = app.mcp_server.request_handlers[types.GetTaskRequest]

            missing_token = request_ctx.set(
                RequestContext(
                    request_id="1",
                    meta=types.RequestParams.Meta(__pydantic_extra__={"tenantId": "acme", "userId": "alice"}),
                    session=None,
                    lifespan_context=None,
                )
            )
            try:
                with pytest.raises(McpError) as missing_exc:
                    await handler(
                        types.GetTaskRequest(
                            method="tasks/get",
                            params=types.GetTaskRequestParams(taskId="missing-task"),
                        )
                    )
            finally:
                request_ctx.reset(missing_token)

            forbidden_token = request_ctx.set(
                RequestContext(
                    request_id="2",
                    meta=types.RequestParams.Meta(__pydantic_extra__={"tenantId": "evil", "userId": "bob"}),
                    session=None,
                    lifespan_context=None,
                )
            )
            try:
                with pytest.raises(McpError) as forbidden_exc:
                    await handler(
                        types.GetTaskRequest(
                            method="tasks/get",
                            params=types.GetTaskRequestParams(taskId=task.id),
                        )
                    )
            finally:
                request_ctx.reset(forbidden_token)

            assert missing_exc.value.error.code == forbidden_exc.value.error.code
            assert missing_exc.value.error.code == types.INVALID_PARAMS
            assert "not found" in missing_exc.value.error.message.lower()
            assert "not found" in forbidden_exc.value.error.message.lower()

        asyncio.run(_run())


class TestExtractTaskAccessContext:
    def test_ignores_spoofed_meta_identity(self):
        rc = RequestContext(
            request_id="1",
            meta=types.RequestParams.Meta(
                __pydantic_extra__={"userId": "u1", "tenantId": "t1", "sessionId": "s1"}
            ),
            session=None,
            lifespan_context=None,
        )
        ctx = extract_task_access_context(rc)
        assert ctx is not None
        assert ctx.user_id is None
        assert ctx.tenant_id is None
        assert ctx.session_id is None

    def test_uses_verified_jwt_not_meta(self):
        from types import SimpleNamespace

        from nitrostack.auth.jwt import JWTService

        jwt = JWTService()
        DIContainer.get_instance().register_value(JWTService, jwt)
        token = jwt.create_token({"sub": "alice", "tenant_id": "acme"})
        rc = RequestContext(
            request_id="1",
            meta=types.RequestParams.Meta(
                __pydantic_extra__={"userId": "eve", "tenantId": "evil"}
            ),
            session=None,
            lifespan_context=None,
            request=SimpleNamespace(headers={"authorization": f"Bearer {token}"}),
        )
        ctx = extract_task_access_context(rc)
        assert ctx is not None
        assert ctx.user_id == "alice"
        assert ctx.tenant_id == "acme"

    def test_failed_jwt_returns_empty_context(self):
        from types import SimpleNamespace

        from nitrostack.auth.jwt import JWTService

        jwt = JWTService()
        DIContainer.get_instance().register_value(JWTService, jwt)
        rc = RequestContext(
            request_id="1",
            meta=None,
            session=None,
            lifespan_context=None,
            request=SimpleNamespace(headers={"authorization": "Bearer not-a-jwt"}),
        )
        ctx = extract_task_access_context(rc)
        assert ctx is not None
        assert ctx.user_id is None
        assert ctx.tenant_id is None
