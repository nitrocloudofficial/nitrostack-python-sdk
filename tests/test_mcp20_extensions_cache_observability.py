"""Tests for MCP 2.0 extensions, cache hints, and observability."""

import asyncio
import os
import sys

import mcp.types as types
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ExecutionContext, injectable, module, resource, tool
from nitrostack.core.additional_decorators import cache
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.di import DIContainer
from nitrostack.protocol.cache_hints import (
    build_list_endpoint_cache_hint_meta,
    resolve_resource_cache_hint_meta,
    resolve_tool_cache_hint_meta,
)
from nitrostack.protocol.contracts import MCP_CACHE_HINT_KEY, build_cache_hint_meta
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.meta import RequestMeta
from nitrostack.protocol.observability import extract_trace_context, trace_context_from_request_meta
from nitrostack.testing import NitroTestingModule


class EchoInput(BaseModel):
    value: str = Field(default="")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestTraceContext:
    def test_extract_bare_keys(self):
        meta = {
            "traceparent": "00-abc-def-01",
            "tracestate": "vendor=1",
            "baggage": "userId=alice",
        }
        trace = extract_trace_context(meta)
        assert trace is not None
        assert trace.traceparent == "00-abc-def-01"
        assert trace.tracestate == "vendor=1"
        assert trace.baggage == "userId=alice"

    def test_extract_prefixed_keys(self):
        meta = {
            "io.modelcontextprotocol/traceparent": "00-prefixed-01",
        }
        trace = extract_trace_context(meta)
        assert trace is not None
        assert trace.traceparent == "00-prefixed-01"

    def test_returns_none_when_empty(self):
        assert extract_trace_context(None) is None
        assert extract_trace_context({}) is None

    def test_from_request_meta(self):
        meta = RequestMeta(traceparent="00-from-meta-01", baggage="tenant=acme")
        trace = trace_context_from_request_meta(meta)
        assert trace is not None
        assert trace.traceparent == "00-from-meta-01"
        assert trace.baggage == "tenant=acme"


class TestCacheHintResolution:
    def test_resolve_tool_from_cache_decorator(self):
        class _Cfg:
            metadata = {}

        @cache(ttl=120)
        async def handler(_input, _ctx):
            return {}

        meta = resolve_tool_cache_hint_meta(_Cfg(), handler)
        assert meta is not None
        assert meta[MCP_CACHE_HINT_KEY]["ttlMs"] == 120_000
        assert meta[MCP_CACHE_HINT_KEY]["cacheScope"] == "private"

    def test_resolve_tool_from_explicit_metadata(self):
        class _Cfg:
            metadata = {"cacheHint": {"ttlMs": 30_000, "cacheScope": "public"}}

        meta = resolve_tool_cache_hint_meta(_Cfg(), None)
        assert meta[MCP_CACHE_HINT_KEY]["ttlMs"] == 30_000
        assert meta[MCP_CACHE_HINT_KEY]["cacheScope"] == "public"

    def test_resolve_resource_from_cache_max_age(self):
        class _Cfg:
            metadata = {"cacheMaxAge": 45}

        meta = resolve_resource_cache_hint_meta(_Cfg())
        assert meta[MCP_CACHE_HINT_KEY]["ttlMs"] == 45_000

    def test_list_endpoint_cache_hint(self):
        meta = build_list_endpoint_cache_hint_meta()
        assert meta[MCP_CACHE_HINT_KEY]["ttlMs"] == 60_000


class TestDiscoveryExtensions:
    def test_custom_extensions_merge(self):
        result = build_discover_result(
            server_name="srv",
            server_version="1.0.0",
            advertise_tasks=False,
            custom_extensions={"acme.corp/enterprise_rbac": "2026-07-28"},
        )
        extensions = result["capabilities"]["extensions"]
        assert "acme.corp/enterprise_rbac" in extensions
        assert MCPExtensionId.TASKS.value not in extensions

    def test_tasks_extension_only_when_advertised(self):
        result = build_discover_result(
            server_name="srv",
            server_version="1.0.0",
            advertise_tasks=True,
        )
        assert MCPExtensionId.TASKS.value in result["capabilities"]["extensions"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 60_000
        assert result["cacheScope"] == "private"


class TestAppIntegration:
    def test_tool_list_includes_cache_hint_from_decorator(self):
        @injectable()
        class CacheController:
            @tool(name="cached_echo", description="cached", input_schema=EchoInput)
            @cache(ttl=90)
            async def cached_echo(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="CacheHints", controllers=[CacheController])
        class CacheModule:
            pass

        async def _run():
            harness = await NitroTestingModule.create(CacheModule)
            handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
            response = await handler(types.ListToolsRequest(method="tools/list", params={}))
            result = response.root
            tool = next(t for t in result.tools if t.name == "cached_echo")
            hint = (tool.meta or {}).get(MCP_CACHE_HINT_KEY)
            assert hint["ttlMs"] == 90_000
            assert result.meta is not None
            assert result.meta[MCP_CACHE_HINT_KEY]["ttlMs"] == 60_000

        asyncio.run(_run())

    def test_resource_list_includes_cache_hint(self):
        @injectable()
        class ResourceController:
            @resource(
                uri="mcp://cache/metrics",
                name="metrics",
                description="metrics",
                metadata={"cacheMaxAge": 10},
            )
            async def metrics(self, context: ExecutionContext) -> str:
                return "{}"

        @module(name="CacheResource", controllers=[ResourceController])
        class ResourceModule:
            pass

        async def _run():
            harness = await NitroTestingModule.create(ResourceModule)
            handler = harness.app.mcp_server.request_handlers[types.ListResourcesRequest]
            response = await handler(types.ListResourcesRequest(method="resources/list", params={}))
            result = response.root
            resource = next(r for r in result.resources if r.name == "metrics")
            hint = (resource.meta or {}).get(MCP_CACHE_HINT_KEY)
            assert hint["ttlMs"] == 10_000

        asyncio.run(_run())

    def test_advertise_tasks_only_when_tool_supports_tasks(self):
        @injectable()
        class SyncController:
            @tool(name="sync_only", description="sync", input_schema=EchoInput, task_support="forbidden")
            async def sync_only(self, input: EchoInput, context: ExecutionContext) -> str:
                return input.value

        @module(name="NoTasksExt", controllers=[SyncController])
        class NoTasksModule:
            pass

        @mcp_app(module=NoTasksModule, server=ServerConfig(name="no-tasks"))
        class NoTasksApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(NoTasksApp)
            assert app._advertise_tasks_extension() is False

        asyncio.run(_run())

    def test_trace_context_attached_on_tool_call(self):
        captured: dict[str, ExecutionContext | None] = {"ctx": None}

        @injectable()
        class TraceController:
            @tool(name="trace_echo", description="trace", input_schema=EchoInput)
            async def trace_echo(self, input: EchoInput, context: ExecutionContext) -> str:
                captured["ctx"] = context
                return input.value

        @module(name="TraceExt", controllers=[TraceController])
        class TraceModule:
            pass

        async def _run():
            from mcp.server.lowlevel.server import request_ctx, RequestContext

            harness = await NitroTestingModule.create(TraceModule)
            handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
            meta = {
                "traceparent": "00-trace-test-01",
                "baggage": "session=abc",
            }
            request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="trace_echo",
                    arguments={"value": "hi"},
                    meta=meta,
                ),
            )
            token = request_ctx.set(
                RequestContext(
                    request_id="req-trace",
                    meta=meta,
                    session=None,
                    lifespan_context={},
                )
            )
            try:
                await handler(request)
            finally:
                request_ctx.reset(token)

            ctx = captured["ctx"]
            assert ctx is not None
            assert ctx.trace is not None
            assert ctx.trace.traceparent == "00-trace-test-01"
            assert ctx.trace.baggage == "session=abc"

        asyncio.run(_run())
