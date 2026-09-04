"""
Phase 4 — Widgets framework tests.

Run: pytest tests/test_widgets.py -v
Or:  python tests/test_widgets.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mcp.types as types
from mcp.server.lowlevel.server import request_ctx, RequestContext
from mcp.server.experimental.request_context import Experimental
from pydantic import BaseModel
from starlette.testclient import TestClient

from nitrostack import (
    ExecutionContext,
    ToolInvocation,
    WidgetCsp,
    WidgetOptions,
    injectable,
    module,
    tool,
    widget,
)
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.core.app_mode import (
    OPENAI_SKYBRIDGE_MIME_TYPE,
    RESOURCE_MIME_TYPE_MCP_APP,
    RESOURCE_MIME_TYPE_OPENAI,
    get_widget_mime_type,
)
from nitrostack.testing import NitroTestingModule
from nitrostack.transports.http import build_http_app
from nitrostack.widgets.component import Component, create_component, load_widget_html
from nitrostack.core.decorators import widget_resource_uri
from nitrostack.widgets.mcp_meta import openai_widget_csp, widget_csp_to_ui_csp

FIXTURES = Path(__file__).parent / "fixtures" / "widgets" / "out"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class EmptyInput(BaseModel):
    pass


class EchoInput(BaseModel):
    value: str = ""


def _tool_meta(tool: types.Tool) -> dict:
    return getattr(tool, "meta", None) or getattr(tool, "_meta", {}) or {}


import contextlib


@contextlib.contextmanager
def app_mode(mode: str | None):
    old = os.environ.get("NITROSTACK_APP_MODE")
    if mode is None:
        os.environ.pop("NITROSTACK_APP_MODE", None)
    else:
        os.environ["NITROSTACK_APP_MODE"] = mode
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("NITROSTACK_APP_MODE", None)
        else:
            os.environ["NITROSTACK_APP_MODE"] = old


def _make_widget_module(
  *,
  with_html_file: bool = False,
  object_form: bool = False,
  task_support: str = "forbidden",
  invocation: ToolInvocation | None = None,
):
    html_path = FIXTURES / "sample.html"
    if with_html_file:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html><body id='w'>widget</body></html>", encoding="utf-8")

    widget_spec = (
        WidgetOptions(
            route="sample",
            prefers_border=True,
            domain="https://app.example.com",
            csp=WidgetCsp(connect_domains=["https://api.example.com"]),
            can_invoke_tools=True,
        )
        if object_form
        else "sample"
    )

    @injectable()
    class WidgetTestController:
        @tool(
            name="plain_tool",
            description="No widget",
            input_schema=EmptyInput,
        )
        async def plain_tool(self, input: EmptyInput, context: ExecutionContext) -> dict:
            return {"ok": True}

        @tool(
            name="widget_tool",
            description="Widget tool",
            input_schema=EchoInput,
            task_support=task_support,
            invocation=invocation,
        )
        @widget(widget_spec)
        async def widget_tool(self, input: EchoInput, context: ExecutionContext) -> dict:
            return {"value": input.value, "rendered": True}

    @module(name="widget_test_mod", controllers=[WidgetTestController])
    class WidgetTestModule:
        pass

    return WidgetTestModule, html_path if with_html_file else None


async def _harness(module_cls, *, chdir_to_fixtures: bool = False):
    if chdir_to_fixtures:
        os.chdir(FIXTURES.parent.parent.parent)  # tests/fixtures
    @mcp_app(module=module_cls, server=ServerConfig(name="widget-test"))
    class _App:
        pass
    return await NitroTestingModule.create(module_cls)


# ---------------------------------------------------------------------------
# Unit: app mode + component
# ---------------------------------------------------------------------------

def test_widget_mime_type_by_mode():
    with app_mode("openai"):
        assert get_widget_mime_type() == RESOURCE_MIME_TYPE_OPENAI
    with app_mode("mcp-app"):
        assert get_widget_mime_type() == RESOURCE_MIME_TYPE_MCP_APP
    with app_mode("universal"):
        assert get_widget_mime_type() == RESOURCE_MIME_TYPE_MCP_APP
    with app_mode(None):
        assert get_widget_mime_type() == RESOURCE_MIME_TYPE_MCP_APP
    assert OPENAI_SKYBRIDGE_MIME_TYPE == "text/html+skybridge"
    assert get_widget_mime_type() != OPENAI_SKYBRIDGE_MIME_TYPE


def test_component_resource_uri_and_bundle():
    c = create_component(id="card", name="Card", html="<div>hi</div>", css="body{}", js="console.log(1)")
    assert c.resource_uri == "ui://widget/card.html"
    assert "<div>hi</div>" in c.get_bundle()
    assert "<style>" in c.get_bundle()
    assert "<script" in c.get_bundle()
    try:
        create_component(id="", name="x", html="")
        raise AssertionError("empty id should raise")
    except ValueError:
        pass


def test_csp_conversion():
    csp = WidgetCsp(
        connect_domains=["https://a.com"],
        resource_domains=["https://b.com"],
        frame_domains=["https://c.com"],
    )
    ui = widget_csp_to_ui_csp(csp)
    assert ui == {
        "connectDomains": ["https://a.com"],
        "resourceDomains": ["https://b.com"],
        "frameDomains": ["https://c.com"],
    }
    oai = openai_widget_csp(csp)
    assert oai == {
        "connect_domains": ["https://a.com"],
        "resource_domains": ["https://b.com"],
        "frame_domains": ["https://c.com"],
    }


def test_load_widget_html_from_file():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / "file-route.html"
    path.write_text("<html>from-file</html>", encoding="utf-8")
    loaded = load_widget_html("file-route", search_paths=[FIXTURES], allow_builtin=False)
    assert loaded == "<html>from-file</html>"


def test_load_widget_html_src_and_dist_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.py").write_text("# project root\n", encoding="utf-8")
        src_out = root / "src" / "widgets" / "out"
        src_out.mkdir(parents=True)
        (src_out / "from-src.html").write_text("<html>src</html>", encoding="utf-8")
        dist_out = root / "dist" / "widgets" / "out"
        dist_out.mkdir(parents=True)
        (dist_out / "from-dist.html").write_text("<html>dist</html>", encoding="utf-8")
        assert load_widget_html("from-src", project_root=root, allow_builtin=False) == "<html>src</html>"
        assert load_widget_html("from-dist", project_root=root, allow_builtin=False) == "<html>dist</html>"


def test_builtin_route_html_and_host_bridge():
    from nitrostack.widgets.host_bridge import HOST_BRIDGE_JS
    from nitrostack.widgets.route_templates import get_builtin_route_html

    html = get_builtin_route_html("pizza-list")
    assert html is not None
    assert "Pizza shops" in html
    assert "ui/notifications/tool-result" in html
    assert "ui/initialize" in html
    assert "__nitroWidgetRender" in html
    assert "shops.forEach" in html
    assert "show_pizza_shop" in html
    assert 'id="nitrostack-tool-data"' in html
    assert "structuredContent" in HOST_BRIDGE_JS
    assert "__nitrostack_readEmbeddedData" in HOST_BRIDGE_JS
    assert "tools/call" in HOST_BRIDGE_JS
    assert "ui/open-link" in HOST_BRIDGE_JS
    assert "ui/request-display-mode" in HOST_BRIDGE_JS
    assert "availableDisplayModes" in HOST_BRIDGE_JS
    assert "__nitrostack_applyTheme" in HOST_BRIDGE_JS
    assert "data-call-tool" in html
    assert "color-scheme" in html


def test_missing_widget_still_registers_resource():
    async def run():
        @injectable()
        class MissingWidgetController:
            @tool(name="ghost_tool", description="No HTML file", input_schema=EmptyInput)
            @widget("definitely-missing-route")
            async def ghost_tool(self, input: EmptyInput, context: ExecutionContext) -> dict:
                return {"ok": True}

        @module(name="missing_widget_mod", controllers=[MissingWidgetController])
        class MissingModule:
            pass

        harness = await NitroTestingModule.create(MissingModule)
        resources = await _list_resources(harness)
        uris = [str(r.uri) for r in resources]
        assert "ui://widget/definitely-missing-route.html" in uris
        resp = await _read_resource_raw(harness, "ui://widget/definitely-missing-route.html")
        text = resp.root.contents[0].text or ""
        assert "missing" in text.lower()

    asyncio.run(run())


def test_output_schema_on_tools_list():
    class EchoOutput(BaseModel):
        value: str
        rendered: bool

    async def run():
        @injectable()
        class SchemaController:
            @tool(
                name="schema_tool",
                description="Has output schema",
                input_schema=EchoInput,
                output_schema=EchoOutput,
            )
            @widget("sample")
            async def schema_tool(self, input: EchoInput, context: ExecutionContext) -> dict:
                return {"value": input.value, "rendered": True}

        @module(name="schema_mod", controllers=[SchemaController])
        class SchemaModule:
            pass

        harness = await NitroTestingModule.create(SchemaModule)
        tools = await _list_tools(harness)
        target = next(t for t in tools if t.name == "schema_tool")
        schema = getattr(target, "outputSchema", None)
        assert isinstance(schema, dict)
        props = schema.get("properties") or {}
        assert "value" in props
        assert "rendered" in props

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Integration: tools/list, resources, tools/call
# ---------------------------------------------------------------------------

async def _list_tools(harness: NitroTestingModule):
    handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
    result = await handler(None)
    return result.root.tools


async def _list_resources(harness: NitroTestingModule):
    handler = harness.app.mcp_server.request_handlers[types.ListResourcesRequest]
    result = await handler(None)
    return result.root.resources


async def _read_resource_raw(harness: NitroTestingModule, uri: str):
    handler = harness.app.mcp_server.request_handlers[types.ReadResourceRequest]
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=uri),
    )
    return await handler(req)


async def _call_tool_raw(harness: NitroTestingModule, name: str, arguments: dict | None = None):
    handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    return await handler(req)


def test_tool_list_meta_openai_mode():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True, object_form=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)  # tests/fixtures — widgets/out/sample.html
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        with app_mode("openai"):
            tools = await _list_tools(harness)
        widget_tool = next(t for t in tools if t.name == "widget_tool")
        plain = next(t for t in tools if t.name == "plain_tool")
        meta = _tool_meta(widget_tool)
        plain_meta = _tool_meta(plain)
        assert meta["ui/template"] == "ui://widget/sample.html"
        assert meta["openai/outputTemplate"] == "ui://widget/sample.html"
        assert "ui" not in meta or "resourceUri" not in (meta.get("ui") or {})
        assert getattr(widget_tool, "outputTemplate", None) == "ui://widget/sample.html"
        assert "openai/outputTemplate" not in plain_meta
        assert "ui" not in plain_meta

    asyncio.run(run())


def test_tool_list_meta_mcp_app_mode():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        with app_mode("mcp-app"):
            tools = await _list_tools(harness)
        widget_tool = next(t for t in tools if t.name == "widget_tool")
        meta = _tool_meta(widget_tool)
        assert meta["ui/template"] == "ui://widget/sample.html"
        assert "openai/outputTemplate" not in meta
        assert meta["ui"]["resourceUri"] == "ui://widget/sample.html"
        assert meta["ui"]["visibility"] == "visible"
        assert getattr(widget_tool, "outputTemplate", None) in (None, "")

    asyncio.run(run())


def test_tool_list_meta_universal_mode():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True, object_form=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        with app_mode("universal"):
            tools = await _list_tools(harness)
        meta = _tool_meta(next(t for t in tools if t.name == "widget_tool"))
        assert meta["openai/outputTemplate"] == "ui://widget/sample.html"
        assert meta["ui"]["resourceUri"] == "ui://widget/sample.html"
        assert meta["ui"]["prefersBorder"] is True
        assert meta["openai/widgetPrefersBorder"] is True
        assert meta["openai/widgetDomain"] == "https://app.example.com"
        assert meta["openai/widgetCSP"]["connect_domains"] == ["https://api.example.com"]
        assert meta["ui"]["csp"]["connectDomains"] == ["https://api.example.com"]
        assert meta["openai/widgetAccessible"] is True

    asyncio.run(run())


def test_invocation_meta_only_in_openai_modes():
    inv = ToolInvocation(invoking="Working…", invoked="Done")

    async def run():
        mod, _ = _make_widget_module(with_html_file=True, invocation=inv)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        with app_mode("mcp-app"):
            meta_mcp = _tool_meta(next(t for t in await _list_tools(harness) if t.name == "widget_tool"))
        with app_mode("openai"):
            meta_oai = _tool_meta(next(t for t in await _list_tools(harness) if t.name == "widget_tool"))
        assert "openai/toolInvocation/invoking" not in meta_mcp
        assert meta_oai.get("openai/toolInvocation/invoking") == "Working…"

    asyncio.run(run())


def test_resources_list_and_read_with_meta():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True, object_form=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        resources = await _list_resources(harness)
        uris = [str(r.uri) for r in resources]
        assert "ui://widget/sample.html" in uris
        with app_mode("universal"):
            resp = await _read_resource_raw(harness, "ui://widget/sample.html")
        contents = resp.root.contents
        assert len(contents) == 1
        text = contents[0].text or contents[0].blob
        assert "widget" in (text or "")
        assert contents[0].mimeType == RESOURCE_MIME_TYPE_MCP_APP
        meta = contents[0].meta or {}
        assert meta.get("openai/widgetPrefersBorder") is True
        assert meta["ui"]["prefersBorder"] is True

    asyncio.run(run())


def test_call_tool_structured_content_and_meta():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)
        with app_mode("universal"):
            resp = await _call_tool_raw(harness, "widget_tool", {"value": "hello"})
            result = resp.root
            assert result.structuredContent == {"value": "hello", "rendered": True}
            assert result.meta["ui"]["resourceUri"] == "ui://widget/sample.html"
            assert result.meta["openai/outputTemplate"] == "ui://widget/sample.html"
            types_found = {getattr(block, "type", None) for block in result.content}
            assert "text" in types_found
            assert "resource" in types_found
            assert "resource_link" in types_found
            embedded = next(b for b in result.content if getattr(b, "type", None) == "resource")
            html = embedded.resource.text
            assert "hello" in html or "nitrostack-tool-data" in html

    asyncio.run(run())


def test_widget_task_result_matches_direct_call():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True, task_support="optional")
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)
            harness = await _harness(mod)
        finally:
            os.chdir(prev)

        handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]

        with app_mode("universal"):
            direct_resp = await handler(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(
                        name="widget_tool",
                        arguments={"value": "task-test"},
                    ),
                )
            )
            direct = direct_resp.root

            task_req = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="widget_tool",
                    arguments={"value": "task-test"},
                    task=types.TaskMetadata(ttl=60),
                ),
            )
            token = request_ctx.set(
                RequestContext(
                    request_id="widget-task-test",
                    meta=None,
                    session=None,
                    lifespan_context=None,
                    experimental=Experimental(task_metadata=task_req.params.task),
                    request=task_req,
                )
            )
            try:
                task_resp = await handler(task_req)
            finally:
                request_ctx.reset(token)

            assert isinstance(task_resp.root, types.CreateTaskResult)
            task_id = task_resp.root.task.taskId

            get_handler = harness.app.mcp_server.request_handlers[types.GetTaskRequest]
            task_payload = None
            for _ in range(50):
                await asyncio.sleep(0.02)
                raw = await get_handler(
                    types.GetTaskRequest(
                        method="tasks/get",
                        params=types.GetTaskRequestParams(taskId=task_id),
                    )
                )
                if raw.status == "completed" and raw.result is not None:
                    task_payload = types.CallToolResult(**raw.result)
                    break
            else:
                raise AssertionError("task did not complete in time")

            assert task_payload.structuredContent == direct.structuredContent
            assert task_payload.meta == direct.meta

    asyncio.run(run())


def test_stateless_http_widget_read_and_call():
    async def run():
        mod, _ = _make_widget_module(with_html_file=True)
        prev = os.getcwd()
        try:
            os.chdir(FIXTURES.parent.parent)

            @mcp_app(module=mod, server=ServerConfig(name="widget-http", stateless=True))
            class _App:
                pass

            app = await McpApplicationFactory.create(_App)
            http_app = build_http_app(app, stateless=True)
        finally:
            os.chdir(prev)

        with app_mode("mcp-app"), TestClient(http_app) as client:
            read_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "ui://widget/sample.html"},
            }
            read_resp = client.post("/mcp", headers=JSON_HEADERS, json=read_body)
            assert read_resp.status_code == 200
            read_json = _parse_sse_or_json(read_resp)
            text = read_json["result"]["contents"][0]["text"]
            assert "widget" in text

            call_body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "widget_tool", "arguments": {"value": "http"}},
            }
            call_resp = client.post("/mcp", headers=JSON_HEADERS, json=call_body)
            assert call_resp.status_code == 200
            call_json = _parse_sse_or_json(call_resp)
            assert call_json["result"]["structuredContent"]["value"] == "http"
            assert call_json["result"]["_meta"]["ui"]["resourceUri"] == "ui://widget/sample.html"

    asyncio.run(run())


def _parse_sse_or_json(resp) -> dict:
    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        payload = None
        for raw_event in resp.text.replace("\r\n", "\n").split("\n\n"):
            for line in raw_event.split("\n"):
                if line.startswith("data:"):
                    data = line[len("data:"):].strip()
                    if data:
                        payload = json.loads(data)
        return payload
    return resp.json()


def test_widget_resource_uri_helper():
    assert widget_resource_uri("calculator-result") == "ui://widget/calculator-result.html"


if __name__ == "__main__":
    test_widget_mime_type_by_mode()
    test_component_resource_uri_and_bundle()
    test_csp_conversion()
    test_load_widget_html_from_file()
    test_load_widget_html_src_and_dist_paths()
    test_builtin_route_html_and_host_bridge()
    test_missing_widget_still_registers_resource()
    test_output_schema_on_tools_list()
    test_tool_list_meta_openai_mode()
    test_tool_list_meta_mcp_app_mode()
    test_tool_list_meta_universal_mode()
    test_invocation_meta_only_in_openai_modes()
    test_resources_list_and_read_with_meta()
    test_call_tool_structured_content_and_meta()
    test_widget_task_result_matches_direct_call()
    test_stateless_http_widget_read_and_call()
    print("All widget tests passed.")
