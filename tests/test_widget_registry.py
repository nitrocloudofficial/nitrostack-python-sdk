"""Widget manifests from real registrations, templates and MCP transports."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import mcp.types as types
from pydantic import BaseModel
import pytest
from starlette.testclient import TestClient

from nitrostack import (
    DIContainer, ExecutionContext, ToolExamples, WidgetCsp, WidgetExample,
    WidgetManifest, WidgetMetadata, WidgetOptions, WidgetRegistry,
    get_widget_registry, module, resource, tool, widget,
)
from nitrostack.core.app import McpApplication, ServerConfig, mcp_app
from nitrostack.core.errors import ResourceNotFoundError
from nitrostack.widgets.mcp_meta import resource_read_contents_meta

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "support" / "widget_registry_probe.py"
URI = "widget://examples"
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "registry-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class Empty(BaseModel):
    pass


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    DIContainer.reset()
    for name in ("NITRO_MCP_PROTOCOL_VERSION", "MCP_STATELESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NITROSTACK_APP_MODE", "universal")
    yield
    DIContainer.reset()


def make_app(specs=(), *, era="auto", collision=False):
    methods = {}
    for name, spec, example in specs:
        async def handler(self, input: Empty, context: ExecutionContext):
            raise AssertionError("Registry construction must not execute tools")
        methods[name] = tool(name, f"Description of {name}", Empty, examples=example)(
            widget(spec)(handler)
        )
    if collision:
        @resource(URI, "Custom", "Custom resource")
        async def custom(self, context: ExecutionContext):
            return "custom"
        methods["custom"] = custom
    provider = type("RegistryTools", (), methods)

    @module(name="registry-tests", providers=[provider])
    class AppModule:
        pass

    @mcp_app(module=AppModule, server=ServerConfig(name="registry-test", protocol_era=era))
    class App:
        pass

    return McpApplication(App)


def test_public_exports_and_empty_app():
    import nitrostack.widgets as widgets
    for symbol in (WidgetExample, WidgetManifest, WidgetMetadata, WidgetRegistry):
        assert getattr(widgets, symbol.__name__) is symbol
    app = make_app()
    assert get_widget_registry(app) is app.widget_registry
    assert widgets.get_widget_registry(app) is app.widget_registry
    assert app.widget_registry.get_manifest().to_dict() == {
        "widgets": [], "count": 0, "loaded": True,
    }
    assert URI not in app._resources
    with pytest.raises(ResourceNotFoundError):
        asyncio.run(app._read_resource(URI))


@pytest.mark.parametrize("spec", [
    "example", "/widget/example.html", WidgetOptions(route="example", html="<p>test</p>"),
    {"route": "example", "html": "<p>test</p>"},
])
def test_decorator_forms_and_resource_consistency(spec):
    app = make_app([("example_tool", spec, None)])
    entry = app.widget_registry.get_all_widgets()[0]
    assert entry.route == "example"
    assert entry.uri == "ui://widget/example.html"
    assert entry.mime_type == "text/html;profile=mcp-app"
    assert entry.examples == []
    assert entry.tool_names == ["example_tool"]
    component = app._tools["example_tool"].component
    contents = asyncio.run(app._read_resource(entry.uri))[0]
    assert contents.content == component.get_bundle()
    assert contents.mime_type == entry.mime_type
    assert contents.meta == resource_read_contents_meta(component)


@pytest.mark.parametrize("mode,mime", [
    ("openai", "text/html"), ("mcp-app", "text/html;profile=mcp-app"),
    ("universal", "text/html;profile=mcp-app"),
])
def test_helper_metadata_and_app_modes(monkeypatch, mode, mime):
    monkeypatch.setenv("NITROSTACK_APP_MODE", mode)

    def options():
        return WidgetOptions(
            route="helper", html="<p>not in manifest</p>", prefers_border=True,
            domain="https://widgets.example.com", can_invoke_tools=True,
            csp=WidgetCsp(connect_domains=["https://api.example.com"]),
        )

    app = make_app([("helper_tool", options(), None)])
    entry = app.widget_registry.get_all_widgets()[0]
    assert entry.mime_type == mime
    assert entry.metadata == resource_read_contents_meta(app._tools["helper_tool"].component)
    assert bool(entry.metadata.get("ui")) == (mode != "openai")
    assert ("openai/widgetCSP" in entry.metadata) == (mode != "mcp-app")
    assert "not in manifest" not in json.dumps(app.widget_registry.get_manifest().to_dict())


def test_duplicates_order_examples_and_copy_isolation():
    original = ToolExamples(input={"nested": [False, None, 0]}, output=[], description="A")
    app = make_app([
        ("z_tool", WidgetOptions(route="shared", description="Z", prefers_border=False), ToolExamples({}, False)),
        ("a_tool", WidgetOptions(route="shared", description="A", prefers_border=True), original),
        ("b_tool", "another", None),
    ])
    registry = app.widget_registry
    expected = registry.get_manifest().to_dict()
    assert [w["route"] for w in expected["widgets"]] == ["another", "shared"]
    shared = registry.get_widget("ui://widget/shared.html")
    assert shared.name == "a_tool" and shared.description == "A"
    assert shared.tool_names == ["a_tool", "z_tool"]
    assert shared.metadata["ui"]["prefersBorder"] is True
    assert [e.tool_name for e in shared.examples] == ["a_tool", "z_tool"]
    assert shared.examples[0].input == {"nested": [False, None, 0]}
    assert shared.examples[0].output == []
    assert shared.examples[1].output is False
    # Neither source declarations nor nested values returned through any API
    # may modify the stored snapshot.
    original.input["nested"].append("changed")
    shared.examples[0].input.clear()
    shared.tool_names.clear()
    registry.get_all_widgets()[1].metadata.clear()
    registry.get_manifest().widgets.clear()
    expected["widgets"][0]["metadata"]["changed"] = True
    assert "changed" not in json.dumps(registry.get_manifest().to_dict())
    assert registry.get_widget("missing") is None


def test_applications_do_not_share_state():
    first = make_app([("one", "one", None)])
    second = make_app([("two", "two", None)])
    assert [w.route for w in first.widget_registry.get_all_widgets()] == ["one"]
    assert [w.route for w in second.widget_registry.get_all_widgets()] == ["two"]


def test_manifest_order_does_not_depend_on_registration_order():
    specs = [
        ("z_tool", WidgetOptions(route="shared", description="Z"), ToolExamples(0, None)),
        ("a_tool", WidgetOptions(route="shared", description="A"), ToolExamples(False, {})),
        ("other", "another", None),
    ]
    first = make_app(specs).widget_registry.get_manifest().to_dict()
    second = make_app(list(reversed(specs))).widget_registry.get_manifest().to_dict()
    assert first == second


def test_manifest_does_not_include_environment_credentials(monkeypatch):
    monkeypatch.setenv("MAPBOX_TOKEN", "widget-registry-secret-sentinel")
    app = make_app([("one", WidgetOptions(route="one", html="<p>html-sentinel</p>"), None)])
    serialized = json.dumps(app.widget_registry.get_manifest().to_dict())
    assert "widget-registry-secret-sentinel" not in serialized
    assert "html-sentinel" not in serialized


def test_reserved_uri_collision_only_when_widgets_exist():
    with pytest.raises(ValueError, match="reserved for the widget manifest"):
        make_app([("one", "one", None)], collision=True)
    app = make_app(collision=True)
    assert asyncio.run(app._read_resource(URI))[0].content == "custom"


def test_additional_server_has_manifest_handlers():
    app = make_app([("one", "one", None)])
    server = app.create_configured_mcp_server()
    listed = asyncio.run(server.request_handlers[types.ListResourcesRequest](
        types.ListResourcesRequest(method="resources/list")
    )).root
    assert URI in {str(entry.uri) for entry in listed.resources}
    read = asyncio.run(server.request_handlers[types.ReadResourceRequest](
        types.ReadResourceRequest(method="resources/read", params=types.ReadResourceRequestParams(uri=URI))
    )).root
    assert json.loads(read.contents[0].text) == app.widget_registry.get_manifest().to_dict()


@pytest.mark.parametrize("era", ["legacy", "modern"])
@pytest.mark.parametrize("has_widgets", [False, True])
def test_http_manifest(era, has_widgets):
    app = make_app([("one", "one", None)] if has_widgets else [], era=era)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    with TestClient(app.get_combined_app(json_response=True)) as client:
        def rpc(method, params, request_id):
            request_headers = {**headers, "Mcp-Method": method}
            if "uri" in params:
                request_headers["Mcp-Name"] = params["uri"]
            response = client.post("/mcp", headers=request_headers, json={
                "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
            })
            assert response.status_code == 200, response.text
            return response.json()

        if era == "legacy":
            init = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            })
            assert init.status_code == 200, init.text
            headers["mcp-session-id"] = init.headers["mcp-session-id"]
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            assert response.status_code == 202
            meta = {}
        else:
            headers["MCP-Protocol-Version"] = "2026-07-28"
            meta = {"_meta": META}
        listed = rpc("resources/list", meta, 2)["result"]["resources"]
        assert (URI in {entry["uri"] for entry in listed}) is has_widgets
        result = rpc("resources/read", {"uri": URI, **meta}, 3)
        if has_widgets:
            contents = result["result"]["contents"][0]
            assert contents["mimeType"] == "application/json"
            assert json.loads(contents["text"]) == app.widget_registry.get_manifest().to_dict()
        else:
            assert "error" in result


@pytest.mark.parametrize("template,routes", [
    ("starter", ["calculator-result"]),
    ("pizzaz", ["pizza-list", "pizza-map", "pizza-shop"]),
    ("flight-booking", ["airport-search", "flight-details", "flight-search-results",
                        "order-cancellation", "order-summary", "seat-selection"]),
])
def test_real_template_bootstrap(template, routes):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "NITROSTACK_APP_MODE": "universal"}
    result = subprocess.run(
        [sys.executable, str(PROBE), template], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert [w["route"] for w in manifest["widgets"]] == routes
    assert manifest["count"] == len(routes) and manifest["loaded"] is True
    for entry in manifest["widgets"]:
        assert entry["uri"] == f"ui://widget/{entry['route']}.html"
        assert entry["mime_type"] == "text/html;profile=mcp-app"
    if template == "flight-booking":
        summary = next(w for w in manifest["widgets"] if w["route"] == "order-summary")
        assert len(summary["tool_names"]) == 2
    if template == "pizzaz":
        for entry in manifest["widgets"]:
            assert entry["metadata"]["ui"]["prefersBorder"] is True
            assert "https://api.mapbox.com" in entry["metadata"]["ui"]["csp"]["connectDomains"]
            assert entry["examples"]


def test_real_stdio_manifest_has_only_protocol_output():
    env = {**os.environ, "PYTHONPATH": str(ROOT), "NITRO_MCP_PROTOCOL_VERSION": "auto"}
    process = subprocess.Popen(
        [sys.executable, str(PROBE), "stdio"], cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    with ThreadPoolExecutor(max_workers=1) as reader:
        try:
            def rpc(method, params, request_id):
                process.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": {**params, "_meta": META},
                }) + "\n")
                process.stdin.flush()
                line = reader.submit(process.stdout.readline).result(timeout=10)
                payload = json.loads(line)
                assert payload["id"] == request_id
                return payload["result"]

            listed = rpc("resources/list", {}, 1)
            assert URI in {entry["uri"] for entry in listed["resources"]}
            contents = rpc("resources/read", {"uri": URI}, 2)["contents"][0]
            assert contents["mimeType"] == "application/json"
            manifest = json.loads(contents["text"])
            assert manifest["count"] == 1
            assert manifest["widgets"][0]["route"] == "stdio-example"
            process.stdin.close()
            process.wait(timeout=10)
            assert process.returncode == 0, process.stderr.read()
            assert process.stdout.read() == ""
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
