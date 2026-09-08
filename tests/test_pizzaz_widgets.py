"""Python-only Pizzaz widgets: HTML files + MCP resource registration."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mcp.types as types
from pydantic import BaseModel

from starlette.testclient import TestClient

from nitrostack import (
    ExecutionContext,
    WidgetCsp,
    WidgetOptions,
    injectable,
    module,
    tool,
    widget,
)
from nitrostack.core.di import DIContainer
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.testing import NitroTestingModule
from nitrostack.transports.http import build_http_app
from nitrostack.widgets.html_util import get_mapbox_token, inject_mapbox_token
from nitrostack.widgets.component import create_component, load_widget_html
from nitrostack.widgets.route_templates import get_builtin_route_html, render_widget_html

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_OUT = ROOT / "nitrostack" / "templates" / "pizzaz" / "widgets" / "out"
ROUTES = ("pizza-list", "pizza-map", "pizza-shop")


def test_pizzaz_html_files_exist_in_template():
    for route in ROUTES:
        path = TEMPLATE_OUT / f"{route}.html"
        assert path.is_file(), f"missing {path}"
        text = path.read_text(encoding="utf-8")
        assert "window.openai" in text
        assert "structuredContent" in text
        assert "openai:set_globals" in text
        assert "ui/notifications/tool-result" in text
        assert "ui/initialize" in text
        assert "__nitroWidgetRender" in text
        assert 'id="nitrostack-tool-data"' in text
        if route == "pizza-list":
            assert "shops.forEach" in text
            assert "show_pizza_shop" in text
        if route == "pizza-map":
            assert "mapbox-gl" in text
            assert "mapboxgl.accessToken" in text
            assert "pk.eyJ" not in text
            assert "data-nitro-needs-client" in text
            assert "Set MAPBOX_TOKEN" in text


def test_pizzaz_builtin_templates_match_disk():
    for route in ROUTES:
        builtin = get_builtin_route_html(route)
        assert builtin is not None
        disk = (TEMPLATE_OUT / f"{route}.html").read_text(encoding="utf-8")
        assert disk == builtin


def test_load_widget_html_walks_from_tool_module():
    fake_module = ROOT / "nitrostack" / "templates" / "pizzaz" / "modules" / "pizzaz" / "pizzaz_tools.py"
    html = load_widget_html("pizza-list", from_file=fake_module)
    assert html is not None
    assert "Pizza shops" in html


class EmptyInput(BaseModel):
    pass


@injectable()
class PizzaListController:
    @tool(
        name="show_pizza_list",
        description="List pizza shops",
        input_schema=EmptyInput,
    )
    @widget(
        WidgetOptions(
            route="pizza-list",
            prefers_border=True,
            csp=WidgetCsp(resource_domains=["https://images.unsplash.com"]),
        )
    )
    async def show_pizza_list(self, input: EmptyInput, context: ExecutionContext) -> dict:
        return {
            "shops": [
                {"id": "a", "name": "Shop A", "rating": 4.5, "address": "1 Main", "priceLevel": 2, "openNow": True},
                {"id": "b", "name": "Shop B", "rating": 4.1, "address": "2 Main", "priceLevel": 1, "openNow": False},
            ],
            "totalShops": 2,
        }


@module(name="pizzaz_widget_test", controllers=[PizzaListController])
class PizzaListModule:
    pass


def test_pizzaz_resources_and_tool_call():
    async def run():
        old = os.environ.get("NITROSTACK_APP_MODE")
        os.environ["NITROSTACK_APP_MODE"] = "universal"
        try:
            harness = await NitroTestingModule.create(PizzaListModule)
            list_handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
            tools = (await list_handler(None)).root.tools
            target = next(t for t in tools if t.name == "show_pizza_list")
            meta = getattr(target, "meta", None) or getattr(target, "_meta", {}) or {}
            assert meta["ui"]["resourceUri"] == "ui://widget/pizza-list.html"
            assert meta["openai/widgetPrefersBorder"] is True
            assert meta["openai/widgetCSP"]["resource_domains"] == ["https://images.unsplash.com"]

            list_res = harness.app.mcp_server.request_handlers[types.ListResourcesRequest]
            resources = (await list_res(None)).root.resources
            uris = {str(r.uri) for r in resources}
            assert "ui://widget/pizza-list.html" in uris

            read_handler = harness.app.mcp_server.request_handlers[types.ReadResourceRequest]
            read = await read_handler(
                types.ReadResourceRequest(
                    method="resources/read",
                    params=types.ReadResourceRequestParams(uri="ui://widget/pizza-list.html"),
                )
            )
            text = read.root.contents[0].text or ""
            assert "Pizza shops" in text
            assert "shops.forEach" in text
            assert "show_pizza_shop" in text

            call_handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
            resp = await call_handler(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name="show_pizza_list", arguments={}),
                )
            )
            result = resp.root
            assert result.structured_content is not None
            assert len(result.structured_content["shops"]) == 2
            assert result.structured_content["totalShops"] == 2
            assert result.meta["ui"]["resourceUri"] == "ui://widget/pizza-list.html"
            assert result.meta["openai/outputTemplate"] == "ui://widget/pizza-list.html"
            embedded = next(b for b in result.content if getattr(b, "type", None) == "resource")
            html = embedded.resource.text
            assert "Shop A" in html
            assert "Shop B" in html
            assert "2 shops" in html or "2 shop" in html
        finally:
            if old is None:
                os.environ.pop("NITROSTACK_APP_MODE", None)
            else:
                os.environ["NITROSTACK_APP_MODE"] = old

    asyncio.run(run())


def test_live_http_widget_preview_calls_tool():
    DIContainer.reset()
    @mcp_app(module=PizzaListModule, server=ServerConfig(name="preview-test"))
    class PreviewApp:
        pass

    app = asyncio.run(McpApplicationFactory.create(PreviewApp))
    http_app = build_http_app(app, enable_cors=True, stateless=True)
    with TestClient(http_app) as client:
        page = client.get("/widgets/preview")
        assert page.status_code == 200
        assert "show_pizza_list" in page.text
        assert "/widgets/preview/call" in page.text
        assert "id=\"args\"" in page.text
        called = client.post("/widgets/preview/call", json={"tool": "show_pizza_list", "arguments": {}})
        assert called.status_code == 200, called.text
        body = called.json()
        assert len(body["structuredContent"]["shops"]) == 2
        assert "Shop A" in body["html"]
        assert "Shop B" in body["html"]
        assert "Pizza shops" in body["html"]
        missing = client.post("/widgets/preview/call", json={"tool": "nope", "arguments": {}})
        assert missing.status_code == 404
        bad_json = client.post(
            "/widgets/preview/call",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert bad_json.status_code == 400
        assert "Invalid JSON" in bad_json.json()["error"]


def _load_template_pizzaz(module_name: str):
    """Import a pizzaz template module without leftover test stubs."""
    template_root = str(ROOT / "nitrostack" / "templates" / "pizzaz")
    for key in list(sys.modules):
        if key == "modules" or key.startswith("modules."):
            del sys.modules[key]
    if template_root not in sys.path:
        sys.path.insert(0, template_root)
    import importlib

    return importlib.import_module(f"modules.pizzaz.{module_name}")


def test_pizzaz_open_now_filter_excludes_closed_shops():
    """Match TS getShopsFiltered({ openNow: true }) — closed shops must drop out."""
    pizzaz_service = _load_template_pizzaz("pizzaz_service")
    service = pizzaz_service.PizzazService()
    all_shops = service.get_all_shops()
    assert any(not shop["openNow"] for shop in all_shops)

    opened = service.get_shops_filtered({"openNow": True})
    assert opened
    assert all(shop["openNow"] for shop in opened)
    assert len(opened) < len(all_shops)

    as_string = service.get_shops_filtered({"openNow": "true"})
    assert [s["id"] for s in as_string] == [s["id"] for s in opened]

    closed_or_all = service.get_shops_filtered({"openNow": False})
    # False / empty / "false" means "don't care" — all shops, not closed-only.
    assert [s["id"] for s in closed_or_all] == [s["id"] for s in all_shops]
    assert [s["id"] for s in service.get_shops_filtered({"openNow": ""})] == [s["id"] for s in all_shops]
    assert [s["id"] for s in service.get_shops_filtered({"openNow": "false"})] == [s["id"] for s in all_shops]


def test_show_pizza_map_blank_filter_returns_all_shops():
    """Inspector sends filter='' — must match TS optional enum + filter || 'all'."""
    from nitrostack.core.app import parse_tool_input

    pizzaz_tools = _load_template_pizzaz("pizzaz_tools")
    parsed = parse_tool_input(pizzaz_tools.ShowMapInput, {"filter": ""})
    assert parsed.filter == "all"
    assert pizzaz_tools.ShowMapInput(filter="").filter == "all"
    assert pizzaz_tools.ShowMapInput(filter="all").filter == "all"


def test_mapbox_token_reads_env_only():
    os.environ.pop("MAPBOX_TOKEN", None)
    os.environ.pop("NEXT_PUBLIC_MAPBOX_TOKEN", None)
    assert get_mapbox_token() == ""
    os.environ["NEXT_PUBLIC_MAPBOX_TOKEN"] = "pk.customtoken123"
    try:
        assert get_mapbox_token() == "pk.customtoken123"
    finally:
        os.environ.pop("NEXT_PUBLIC_MAPBOX_TOKEN", None)


def test_resources_read_bundle_injects_mapbox_token():
    """Studio loads widgets via resources/read → get_bundle, not tools/call HTML."""
    os.environ.pop("MAPBOX_TOKEN", None)
    os.environ.pop("NEXT_PUBLIC_MAPBOX_TOKEN", None)
    disk = (TEMPLATE_OUT / "pizza-map.html").read_text(encoding="utf-8")
    assert 'window.__NITRO_MAPBOX_TOKEN = ""' in disk
    component = create_component(id="pizza-map", name="Pizza map", html=disk)
    assert 'window.__NITRO_MAPBOX_TOKEN = ""' in component.get_bundle()

    os.environ["MAPBOX_TOKEN"] = "pk.customtoken123"
    try:
        bundle = component.get_bundle()
        assert 'window.__NITRO_MAPBOX_TOKEN = "pk.customtoken123"' in bundle
        filled = component.html_with_data(
            {
                "shops": [{"id": "tonys-pizza", "name": "Tony's", "coords": [-122.4, 37.7]}],
                "filter": "all",
                "totalShops": 1,
            }
        )
        assert 'window.__NITRO_MAPBOX_TOKEN = "pk.customtoken123"' in filled
        assert inject_mapbox_token(disk).count("pk.customtoken123") == 1
    finally:
        os.environ.pop("MAPBOX_TOKEN", None)


def test_pizza_map_live_html_uses_mapbox():
    html = render_widget_html(
        "pizza-map",
        {
            "shops": [
                {
                    "id": "tonys-pizza",
                    "name": "Tony's New York Pizza",
                    "address": "123 Main St",
                    "coords": [-122.4194, 37.7749],
                    "rating": 4.5,
                }
            ],
            "filter": "all",
            "totalShops": 1,
        },
    )
    assert html is not None
    assert "mapbox-gl.js" in html
    assert "mapboxgl.accessToken" in html
    assert "Tony" in html
    assert 'id="map"' in html
    assert "map-live" in html
    assert "pk.eyJ" not in html
    assert "NavigationControl" in html
    assert "__nitroMapSig" in html


if __name__ == "__main__":
    test_pizzaz_html_files_exist_in_template()
    test_pizzaz_builtin_templates_match_disk()
    test_load_widget_html_walks_from_tool_module()
    test_pizzaz_resources_and_tool_call()
    test_live_http_widget_preview_calls_tool()
    test_pizzaz_open_now_filter_excludes_closed_shops()
    test_show_pizza_map_blank_filter_returns_all_shops()
    test_mapbox_token_reads_env_only()
    test_resources_read_bundle_injects_mapbox_token()
    test_pizza_map_live_html_uses_mapbox()
    print("pizzaz widget tests passed")
