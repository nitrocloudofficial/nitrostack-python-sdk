"""Blueprint conformance verification."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import patch

import mcp.types as types
import pytest
from mcp.server.experimental.request_context import Experimental
from mcp.server.lowlevel.server import request_ctx, RequestContext
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.auth.cimd import assert_safe_fetch_target, is_blocked_ip, resolve_cimd
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import ResourceNotFoundError, TaskNotFoundError
from nitrostack.core.task import TaskManager
from nitrostack.protocol.constants import LEGACY_SESSION_HEADER, MAX_CIMD_BYTES
from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.jsonrpc import map_exception_to_jsonrpc
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.runtime.conformance import (
    BLUEPRINT_CONFORMANCE_AREAS,
    ConformanceArea,
    DEPRECATED_MODERN_METHODS,
    MODERN_JSONRPC_ERROR_CODES,
    REQUIRED_BLUEPRINT_MODULES,
    assert_blueprint_layout,
    protocol_version_matches_blueprint,
    verify_package_layout,
)
from nitrostack.runtime.stateless import assert_stateless_headers
from nitrostack.tasks.eviction import should_evict_terminal_task
from nitrostack.tasks.types import TaskAccessContext, TaskEntry, TaskWireData, datetime_to_ms, utc_now
from nitrostack.tasks.authorization import check_task_access
from nitrostack.transports.cors import cors_preflight_response_headers
from nitrostack.transports.dispatch import IngressContext, StatelessIngressPipeline


class EchoInput(BaseModel):
    value: str = Field(default="")


JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestBlueprintRegistry:
    def test_conformance_areas_defined(self):
        assert set(BLUEPRINT_CONFORMANCE_AREAS) == set(ConformanceArea)
        assert len(BLUEPRINT_CONFORMANCE_AREAS) == 5

    def test_modern_protocol_version(self):
        assert protocol_version_matches_blueprint()
        assert MODERN_PROTOCOL_VERSION == "2026-07-28"

    def test_required_modules_import(self):
        assert verify_package_layout() == []
        assert_blueprint_layout()

    def test_blueprint_module_count(self):
        assert len(REQUIRED_BLUEPRINT_MODULES) >= 17


class TestStatelessHttpConformance:
    def test_discover_advertises_modern_protocol_and_extensions(self):
        result = build_discover_result(
            server_name="blueprint-server",
            server_version="1.0.0",
            advertise_tasks=True,
        )
        assert result["protocolVersion"] == MODERN_PROTOCOL_VERSION
        extensions = result["capabilities"]["extensions"]
        assert MCPExtensionId.TASKS.value in extensions
        assert result["resultType"] == "complete"
        assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
        assert result["cacheScope"] in ("public", "private")

    def test_stateless_pipeline_handles_discover_without_session(self):
        async def _run():
            pipeline = StatelessIngressPipeline(
                IngressContext("srv", "1.0.0", MODERN_PROTOCOL_VERSION),
                discover_handler=lambda _req: build_discover_result(
                    server_name="srv",
                    server_version="1.0.0",
                    protocol_version=MODERN_PROTOCOL_VERSION,
                ),
            )
            body = json.dumps(
                {"jsonrpc": "2.0", "id": "d1", "method": "server/discover", "params": {}}
            ).encode()
            status, resp = await pipeline.handle_post(body, {})
            assert status == 200
            assert resp["result"]["protocolVersion"] == MODERN_PROTOCOL_VERSION
            assert_stateless_headers({"Content-Type": "application/json"})

        asyncio.run(_run())

    def test_post_mcp_stateless_no_session_header(self):
        @injectable()
        class PingController:
            @tool(name="echo", description="echo", input_schema=EchoInput)
            async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="BlueprintHttp", controllers=[PingController])
        class HttpModule:
            pass

        @mcp_app(module=HttpModule, server=ServerConfig(name="blueprint-http", stateless=True))
        class HttpApp:
            pass

        app = asyncio.run(McpApplicationFactory.create(HttpApp))
        http_app = app.get_combined_app(stateless=True, json_response=True)
        with TestClient(http_app) as client:
            resp = client.post(
                "/mcp",
                headers=JSON_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"value": "ok"}},
                },
            )
        assert resp.status_code == 200
        assert LEGACY_SESSION_HEADER not in {k.title() for k in resp.headers.keys()}
        assert resp.json()["result"]["content"][0]["text"] == "ok"

    def test_cors_preflight_includes_mcp_headers(self):
        headers = cors_preflight_response_headers({"Origin": "https://app.example.com"})
        assert "Access-Control-Allow-Origin" in headers
        assert "Mcp-Method" in headers.get("Access-Control-Allow-Headers", "")


class TestJsonRpcConformance:
    @pytest.mark.parametrize(
        "code",
        sorted(MODERN_JSONRPC_ERROR_CODES),
    )
    def test_standard_error_codes_registered(self, code: JsonRpcErrorCode):
        assert int(code) in MODERN_JSONRPC_ERROR_CODES

    def test_missing_resource_maps_to_invalid_params(self):
        resp = map_exception_to_jsonrpc(ResourceNotFoundError("mcp://missing"), "99")
        assert resp["error"]["code"] == JsonRpcErrorCode.INVALID_PARAMS

    @pytest.mark.parametrize("method", sorted(DEPRECATED_MODERN_METHODS))
    def test_deprecated_methods_have_modern_rejection_message(self, method: str):
        message = deprecated_method_message(method)
        assert "2026-07-28" in message or "tasks/get" in message

    def test_tasks_result_rejected_on_modern_wire(self):
        @injectable()
        class DummyController:
            @tool(name="noop", description="noop", input_schema=EchoInput)
            async def noop(self, input: EchoInput, context: ExecutionContext) -> str:
                return "ok"

        @module(name="BlueprintJsonRpc", controllers=[DummyController])
        class JsonRpcModule:
            pass

        @mcp_app(module=JsonRpcModule, server=ServerConfig(name="jsonrpc"))
        class JsonRpcApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(JsonRpcApp)
            handler = app.mcp_server.request_handlers[types.GetTaskPayloadRequest]
            with pytest.raises(McpError) as exc:
                await handler(
                    types.GetTaskPayloadRequest(
                        method="tasks/result",
                        params=types.GetTaskPayloadRequestParams(taskId="missing"),
                    )
                )
            assert exc.value.error.code == types.METHOD_NOT_FOUND

        asyncio.run(_run())


class TestTaskSubsystemConformance:
    def test_task_augmented_call_returns_working_task(self):
        @injectable()
        class AsyncController:
            @tool(name="slow", description="slow", input_schema=EchoInput, task_support="optional")
            async def slow(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="BlueprintTasks", controllers=[AsyncController])
        class TasksModule:
            pass

        @mcp_app(module=TasksModule, server=ServerConfig(name="tasks"))
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
                created = await app._call_tool("slow", {"value": "async"})
                assert isinstance(created, types.CreateTaskResult)
                assert created.task.status == "working"

                get_handler = app.mcp_server.request_handlers[types.GetTaskRequest]
                snapshot = await get_handler(
                    types.GetTaskRequest(
                        method="tasks/get",
                        params=types.GetTaskRequestParams(taskId=created.task.taskId),
                    )
                )
                assert snapshot.status in ("working", "completed")
            finally:
                request_ctx.reset(token)

        asyncio.run(_run())

    def test_tasks_get_embeds_result_after_completion(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(tool_name="job")
            await manager.complete_task(task.id, {"done": True})

            @injectable()
            class DummyController:
                @tool(name="noop_bp", description="noop", input_schema=EchoInput)
                async def noop_bp(self, input: EchoInput, context: ExecutionContext) -> str:
                    return "ok"

            @module(name="BlueprintGet", controllers=[DummyController])
            class GetModule:
                pass

            @mcp_app(module=GetModule, server=ServerConfig(name="get"))
            class GetApp:
                pass

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
            assert payload["result"] is not None

        asyncio.run(_run())

    def test_cancel_marks_task_cancelled(self):
        async def _run():
            from nitrostack.core.task import TaskStatus

            manager = TaskManager()
            task = await manager.create_task(tool_name="job")
            await manager.cancel_task(task.id)
            snapshot = await manager.get_task(task.id)
            assert snapshot.status == TaskStatus.CANCELLED

        asyncio.run(_run())

    def test_terminal_ttl_eviction_uses_last_updated_at(self):
        now = utc_now()
        wire = TaskWireData(
            task_id="evict-me",
            status="completed",
            created_at=now,
            last_updated_at=now,
            ttl_ms=500,
        )
        entry = TaskEntry(task_id="evict-me", data=wire, status="completed")
        before_ttl = datetime_to_ms(now) + 100
        after_ttl = datetime_to_ms(now) + 2_000
        assert should_evict_terminal_task(entry, before_ttl) is False
        assert should_evict_terminal_task(entry, after_ttl) is True


class TestMultiTenantIsolationConformance:
    def test_cross_tenant_access_raises_not_found(self):
        entry = TaskEntry(
            task_id="iso-1",
            data=TaskWireData(task_id="iso-1"),
            owner_id="alice",
            tenant_id="tenant-a",
        )
        foreign = TaskAccessContext(user_id="alice", tenant_id="tenant-b")
        with pytest.raises(TaskNotFoundError):
            check_task_access(entry, foreign)

    def test_tasks_get_cross_tenant_returns_invalid_params(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(
                tool_name="secret",
                owner_id="alice",
                tenant_id="tenant-a",
            )

            @injectable()
            class DummyController:
                @tool(name="noop_iso", description="noop", input_schema=EchoInput)
                async def noop_iso(self, input: EchoInput, context: ExecutionContext) -> str:
                    return "ok"

            @module(name="BlueprintIso", controllers=[DummyController])
            class IsoModule:
                pass

            @mcp_app(module=IsoModule, server=ServerConfig(name="iso"))
            class IsoApp:
                pass

            app = await McpApplicationFactory.create(IsoApp)
            app.task_manager = manager
            handler = app.mcp_server.request_handlers[types.GetTaskRequest]
            token = request_ctx.set(
                RequestContext(
                    request_id="iso",
                    meta={"tenantId": "tenant-b", "userId": "alice"},
                    session=None,
                    lifespan_context={},
                )
            )
            try:
                with pytest.raises(McpError) as exc:
                    await handler(
                        types.GetTaskRequest(
                            method="tasks/get",
                            params=types.GetTaskRequestParams(taskId=task.id),
                        )
                    )
                assert exc.value.error.code == types.INVALID_PARAMS
            finally:
                request_ctx.reset(token)

        asyncio.run(_run())


class TestCimdSsrfConformance:
    @pytest.mark.parametrize(
        "ip",
        ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fc00::1"],
    )
    def test_blocks_special_use_ips(self, ip: str):
        assert is_blocked_ip(ip) is True

    def test_blocks_private_dns_target(self):
        async def _run():
            import socket

            url = "https://metadata.example.com/oauth/client.json"
            with patch(
                "socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
            ):
                from nitrostack.auth.cimd import CimdFetchError

                with pytest.raises(CimdFetchError, match="blocked IP"):
                    await assert_safe_fetch_target(url)

        asyncio.run(_run())

    def test_rejects_oversized_payload(self):
        async def _run():
            from nitrostack.auth.cimd import CimdFetchError

            url = "https://app.example.com/oauth/client-metadata.json"
            oversized = b"x" * (MAX_CIMD_BYTES + 1)

            with patch("nitrostack.auth.cimd.assert_safe_fetch_target", return_value=None):
                with patch("nitrostack.auth.cimd._fetch_cimd_bytes", return_value=oversized):
                    with pytest.raises(CimdFetchError, match="maximum size"):
                        await resolve_cimd(url)

        asyncio.run(_run())
