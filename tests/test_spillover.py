"""Issue #67: bounded results, caller isolation, and safe spillover stores."""

import asyncio
import gc
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import mcp.types as types
import pytest
from pydantic import BaseModel

from nitrostack import (
    AuthContext, DataSpilloverInterceptor, DIContainer, ExecutionContext,
    FileSpilloverStore, MemorySpilloverStore, PayloadTooLargeError,
    ResourceNotFoundError, generate_preview, module, tool, use_interceptors,
)
from nitrostack.core.app import McpApplication
from nitrostack.core.interceptors.data_spillover import (
    SpilloverRegistry, SpilloverResult, caller_owner,
)
from nitrostack.protocol.mrtr import InputRequiredResult
from nitrostack.runtime.request_ctx import RequestContext, request_ctx


@pytest.fixture(autouse=True)
def reset_container():
    DIContainer.reset()
    yield
    DIContainer.reset()


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    cls = MemorySpilloverStore if request.param == "memory" else FileSpilloverStore
    args = () if request.param == "memory" else (tmp_path / "results",)
    return cls(*args, max_bytes=4096, max_total_bytes=16384, ttl_seconds=10)


def test_store_roundtrip_owner_delete(store):
    async def scenario():
        key = await store.put(b'{"answer":42}', owner="alice", mime_type="application/json")
        assert await store.get(key, owner="bob") is None
        assert not await store.delete(key, owner="bob")
        entry = await store.get(key, owner="alice")
        assert entry.payload == b'{"answer":42}'
        assert entry.mime_type == "application/json"
        assert await store.delete(key, owner="alice")
        assert await store.get(key, owner="alice") is None
    asyncio.run(scenario())


def test_store_expiry_and_payload_limit(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])
    async def scenario():
        key = await store.put(b"hello", owner="alice", mime_type="text/plain")
        with pytest.raises(PayloadTooLargeError):
            await store.put(b"x" * 4097, owner="alice", mime_type="text/plain")
        clock[0] += 10
        assert await store.cleanup_expired() == 1
        assert await store.get(key, owner="alice") is None
    asyncio.run(scenario())


def test_memory_lru_and_total_capacity():
    store = MemorySpilloverStore(max_bytes=8, max_total_bytes=8)
    async def scenario():
        first = await store.put(b"1111", owner="a", mime_type="text/plain")
        second = await store.put(b"2222", owner="a", mime_type="text/plain")
        await store.get(first, owner="a")
        await store.put(b"3333", owner="a", mime_type="text/plain")
        assert await store.get(second, owner="a") is None
        assert await store.get(first, owner="a") is not None
        assert store._size == 8
    asyncio.run(scenario())


def test_file_capacity_restart_cleanup(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])
    store = FileSpilloverStore(tmp_path, max_total_bytes=300, ttl_seconds=5)
    async def scenario():
        key = await store.put(b"x" * 80, owner="a", mime_type="text/plain")
        with pytest.raises(ValueError, match="capacity"):
            await store.put(b"x" * 80, owner="a", mime_type="text/plain")
        reopened = FileSpilloverStore(tmp_path, max_total_bytes=300)
        assert (await reopened.get(key, owner="a")).payload == b"x" * 80
        assert list(tmp_path.glob("*.tmp")) == []
        clock[0] += 5
        FileSpilloverStore(tmp_path)
        assert list(tmp_path.glob("*.json")) == []
    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["../escape", "..\\escape", "%2e%2e%2fescape", "/etc/passwd", "C:\\escape", "x?y"])
def test_file_rejects_unsafe_ids(tmp_path, key):
    store = FileSpilloverStore(tmp_path)
    async def scenario():
        with pytest.raises(ValueError):
            await store.get(key, owner="a")
        with pytest.raises(ValueError):
            await store.delete(key, owner="a")
    asyncio.run(scenario())


def test_file_rejects_symlink_escape(tmp_path):
    root = tmp_path / "results"
    store = FileSpilloverStore(root)
    outside = tmp_path / "private.json"
    outside.write_text("private")
    try:
        (root / ("a" * 32 + ".json")).symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS privileges")
    with pytest.raises(ValueError, match="escapes"):
        asyncio.run(store.get("a" * 32, owner="a"))
    assert outside.read_text() == "private"


def test_concurrent_store_operations(store):
    async def scenario():
        keys = await asyncio.gather(*[
            store.put(str(i).encode(), owner="a", mime_type="text/plain") for i in range(20)
        ])
        assert len(set(keys)) == 20
        entries = await asyncio.gather(*[store.get(key, owner="a") for key in keys])
        assert [entry.payload for entry in entries] == [str(i).encode() for i in range(20)]
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [list(range(20)), {str(i): i for i in range(20)}, "é" * 10000,
                                  [{"huge": "x" * 10000}], {"huge": ["x" * 10000]}],
                         ids=["list", "dict", "unicode", "nested-list", "nested-dict"])
def test_previews_are_bounded(value):
    preview = generate_preview(value)
    assert preview["count"] == len(value)
    assert preview["truncated"]
    assert len(json.dumps(preview, ensure_ascii=False).encode()) <= 2048


def test_threshold_boundary_and_full_payload(store):
    interceptor = DataSpilloverInterceptor(threshold_bytes=4, store=store)
    context = ExecutionContext(request_id="1", auth=AuthContext(subject="alice"), logger=MagicMock())
    async def scenario():
        for value in ["éé", {"a": 1}, [1, 2]]:
            async def handler():
                return value
            result = await interceptor.intercept(context, handler)
            if value == "éé":
                assert result is value
            else:
                assert isinstance(result, SpilloverResult)
                entry = await store.get(result.resource_uri.rsplit("/", 1)[1], owner=caller_owner(context))
                assert json.loads(entry.payload) == value
                assert result.original_size_bytes == len(entry.payload)
        async def unicode_handler():
            return "ééé"
        assert (await interceptor.intercept(context, unicode_handler)).original_size_bytes == 6
    asyncio.run(scenario())


def test_control_results_and_missing_identity():
    interceptor = DataSpilloverInterceptor(threshold_bytes=1)
    context = ExecutionContext(request_id="1", logger=MagicMock())
    async def scenario():
        controls = [types.CallToolResult(content=[], isError=True),
                    {"resultType": "input_required", "message": "more"},
                    InputRequiredResult(input_requests=[], request_state={}, message="more")]
        for value in controls:
            async def handler():
                return value
            assert await interceptor.intercept(context, handler) is value
        async def large():
            return "large"
        with pytest.raises(PermissionError):
            await interceptor.intercept(context, large)
    asyncio.run(scenario())


class Session:
    pass


class ToolInput(BaseModel):
    text: str = "large result"


def make_app(interceptor, *, task_support="forbidden", output_schema=None):
    class Tools:
        @tool(name="large", description="Test spillover", input_schema=ToolInput,
              task_support=task_support, output_schema=output_schema)
        @use_interceptors(interceptor)
        async def large(self, input, context):
            return {"text": input.text}
    @module(name="spillover-test", providers=[Tools])
    class Root:
        pass
    return McpApplication(Root)


async def call(app, rc, text="large result", task=None):
    token = request_ctx.set(rc)
    try:
        if task is not None:
            return await app._call_tool("large", {"text": text}, task=task)
        handler = app.mcp_server.request_handlers[types.CallToolRequest]
        return (await handler(types.CallToolRequest(
            method="tools/call", params=types.CallToolRequestParams(name="large", arguments={"text": text}),
        ))).root
    finally:
        request_ctx.reset(token)


async def read(app, rc, uri):
    token = request_ctx.set(rc)
    try:
        handler = app.mcp_server.request_handlers[types.ReadResourceRequest]
        return (await handler(types.ReadResourceRequest(
            method="resources/read", params=types.ReadResourceRequestParams(uri=uri),
        ))).root.contents[0].text
    finally:
        request_ctx.reset(token)


def test_mcp_resource_roundtrip_and_session_isolation(store):
    app = make_app(DataSpilloverInterceptor(threshold_bytes=1, store=store))
    alice = RequestContext(session=Session())
    bob = RequestContext(session=Session())
    async def scenario():
        result = await call(app, alice)
        assert not result.is_error
        data = result.structured_content
        assert result.content[1].type == "resource_link"
        assert json.loads(await read(app, alice, data["resource_uri"])) == {"text": "large result"}
        for caller in [bob, None]:
            with pytest.raises(ResourceNotFoundError):
                await read(app, caller, data["resource_uri"])
        with pytest.raises(ResourceNotFoundError):
            await read(app, alice, "resource://data-spillover/../escape")
        assert data["original_size_bytes"] == len(b'{"text":"large result"}')
    asyncio.run(scenario())


def test_principal_tenant_issuer_isolation(monkeypatch):
    payloads = {
        "alice": {"sub": "alice", "iss": "issuer", "tenant_id": "one"},
        "bob": {"sub": "bob", "iss": "issuer", "tenant_id": "one"},
        "tenant": {"sub": "alice", "iss": "issuer", "tenant_id": "two"},
        "issuer": {"sub": "alice", "iss": "other", "tenant_id": "one"},
    }
    monkeypatch.setattr("nitrostack.auth.request.verify_bearer_payload", payloads.get)
    app = make_app(DataSpilloverInterceptor(threshold_bytes=1))
    def rc(token):
        return RequestContext(request=SimpleNamespace(headers={"authorization": "Bearer " + token}),
                              session=Session(), meta={"userId": "alice", "tenantId": "one"})
    async def scenario():
        result = await call(app, rc("alice"))
        uri = result.structured_content["resource_uri"]
        assert json.loads(await read(app, rc("alice"), uri)) == {"text": "large result"}
        for other in ["bob", "tenant", "issuer", "invalid"]:
            with pytest.raises(ResourceNotFoundError):
                await read(app, rc(other), uri)
        assert (await call(app, rc("invalid"))).is_error
    asyncio.run(scenario())


def test_class_decorator_custom_prefix_and_schema():
    class Configured(DataSpilloverInterceptor):
        def __init__(self):
            super().__init__(threshold_bytes=1, resource_uri_prefix="resource://custom/")
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    app = make_app(Configured, output_schema=schema)
    async def scenario():
        rc = RequestContext(session=Session())
        result = await call(app, rc)
        assert result.structured_content["resource_uri"].startswith("resource://custom/")
        definition = app._build_tool_definition(app._tools["large"])
        original = definition.output_schema["anyOf"][0]
        assert all(original[key] == value for key, value in schema.items())
        assert definition.output_schema["anyOf"][1]["required"] == ["preview", "resource_uri", "original_size_bytes"]
    asyncio.run(scenario())


def test_registry_conflicts_and_session_cleanup():
    registry = SpilloverRegistry()
    interceptor = DataSpilloverInterceptor()
    registry.register(interceptor)
    registry.register(interceptor)
    with pytest.raises(ValueError, match="Conflicting"):
        registry.register(DataSpilloverInterceptor())
    rc = RequestContext(session=Session())
    assert registry.session_key(rc) == registry.session_key(rc)
    del rc
    gc.collect()
    assert registry._sessions == {}


def test_mcp_expiry_and_store_errors(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])
    app = make_app(DataSpilloverInterceptor(threshold_bytes=1, store=store))
    rc = RequestContext(session=Session())
    async def scenario():
        result = await call(app, rc)
        uri = result.structured_content["resource_uri"]
        clock[0] += 10
        with pytest.raises(ResourceNotFoundError):
            await read(app, rc, uri)
        oversized = await call(app, rc, text="x" * 5000)
        assert oversized.is_error
        assert "max_bytes" in oversized.content[0].text
        assert "x" * 5000 not in oversized.content[0].text
    asyncio.run(scenario())


def test_models_mcp_results_and_widget_bypass(store):
    interceptor = DataSpilloverInterceptor(threshold_bytes=1, store=store)
    context = ExecutionContext(request_id="1", auth=AuthContext(subject="alice"), logger=MagicMock())
    app = make_app(interceptor)
    async def scenario():
        for value in [ToolInput(text="full"), types.CallToolResult(
            content=[types.TextContent(type="text", text="full")], structuredContent={"text": "full"},
        )]:
            async def handler():
                return value
            spilled = await interceptor.intercept(context, handler)
            payload = await store.get(spilled.resource_uri.rsplit("/", 1)[1], owner=caller_owner(context))
            assert json.loads(payload.payload) == value.model_dump(mode="json", by_alias=True, exclude_none=True)
            component = MagicMock()
            result = app._to_call_tool_result(spilled, component, context)
            assert result.structured_content["resource_uri"] == spilled.resource_uri
            component.transformer.assert_not_called()
    asyncio.run(scenario())


def test_background_task_spills_and_can_be_read():
    app = make_app(DataSpilloverInterceptor(threshold_bytes=1), task_support="optional")
    rc = RequestContext(session=Session())
    async def scenario():
        result = await call(app, rc, task=types.TaskMetadata(ttl=60000))
        for _ in range(100):
            entry = await app.task_manager._store.get(result.task.task_id)
            if entry.result is not None:
                break
            await asyncio.sleep(0.01)
        assert entry.result is not None
        stored = entry.result
        data = stored.structured_content if isinstance(stored, types.CallToolResult) else stored["structuredContent"]
        assert json.loads(await read(app, rc, data["resource_uri"])) == {"text": "large result"}
    asyncio.run(scenario())


def test_opaque_oauth_identity_reads_resources(monkeypatch):
    from nitrostack.auth.oauth import OAuthService
    from unittest.mock import AsyncMock

    monkeypatch.setattr("nitrostack.auth.request.verify_bearer_payload", lambda token: None)
    service = MagicMock()
    service.introspect_token = AsyncMock(return_value={"active": True, "sub": "alice", "iss": "oauth"})
    DIContainer.get_instance().register_value(OAuthService, service)
    app = make_app(DataSpilloverInterceptor(threshold_bytes=1))
    rc = RequestContext(request=SimpleNamespace(headers={"authorization": "Bearer opaque"}))
    async def scenario():
        result = await call(app, rc)
        uri = result.structured_content["resource_uri"]
        assert json.loads(await read(app, rc, uri)) == {"text": "large result"}
        service.introspect_token.return_value = {"active": False}
        with pytest.raises(ResourceNotFoundError):
            await read(app, rc, uri)
    asyncio.run(scenario())


def test_file_symlink_check_without_os_privileges(tmp_path, monkeypatch):
    from pathlib import Path

    store = FileSpilloverStore(tmp_path)
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "a" * 32 + ".json")
    with pytest.raises(ValueError, match="escapes"):
        asyncio.run(store.get("a" * 32, owner="a"))


@pytest.mark.parametrize("kwargs", [{"threshold_bytes": -1}, {"threshold_bytes": 0.5},
                                    {"resource_uri_prefix": "../results/"},
                                    {"resource_uri_prefix": "resource://results/?x/"}])
def test_invalid_interceptor_configuration(kwargs):
    with pytest.raises(ValueError):
        DataSpilloverInterceptor(**kwargs)
