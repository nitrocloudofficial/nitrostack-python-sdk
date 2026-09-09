import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import injectable, tool, widget, module, ExecutionContext, WidgetOptions
from nitrostack.core.decorators import widget_resource_uri
from nitrostack.testing import NitroTestingModule
from pydantic import BaseModel
import mcp.types as types


class DummyInput(BaseModel):
    pass


class DummyOutput(BaseModel):
    status: str


@injectable()
class WidgetController:
    @tool(
        name="widget_tool",
        description="A tool with a widget",
        input_schema=DummyInput,
        output_schema=DummyOutput,
    )
    @widget("my-custom-widget-route")
    async def widget_tool(self, input: DummyInput, context: ExecutionContext) -> dict:
        return {"status": "ok"}

    @tool(
        name="widget_tool_object",
        description="Widget via WidgetOptions",
        input_schema=DummyInput,
    )
    @widget(WidgetOptions(route="object-route", prefers_border=True))
    async def widget_tool_object(self, input: DummyInput, context: ExecutionContext) -> dict:
        return {"status": "ok"}


@module(name="widget_test", controllers=[WidgetController])
class WidgetTestModule:
    pass


def _tool_meta(tool: types.Tool) -> dict:
    return getattr(tool, "meta", None) or getattr(tool, "_meta", {}) or {}


async def _list_tools_for_mode(mode: str | None):
    old = os.environ.get("NITROSTACK_APP_MODE")
    if mode is None:
        os.environ.pop("NITROSTACK_APP_MODE", None)
    else:
        os.environ["NITROSTACK_APP_MODE"] = mode
    try:
        harness = await NitroTestingModule.create(WidgetTestModule)
        handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
        result = await handler(None)
        return result.root.tools
    finally:
        if old is None:
            os.environ.pop("NITROSTACK_APP_MODE", None)
        else:
            os.environ["NITROSTACK_APP_MODE"] = old


def test_widget_metadata_openai_mode():
    tools = asyncio.run(_list_tools_for_mode("openai"))
    target = next(t for t in tools if t.name == "widget_tool")
    meta = _tool_meta(target)
    uri = "ui://widget/my-custom-widget-route.html"
    assert meta.get("ui/template") == uri
    assert meta.get("openai/outputTemplate") == uri
    assert "ui" not in meta
    assert (
        getattr(target, "output_template", None)
        or getattr(target, "outputTemplate", None)
        or meta.get("openai/outputTemplate")
    ) == uri
    schema = getattr(target, "output_schema", None) or getattr(target, "outputSchema", None)
    assert isinstance(schema, dict)
    assert "status" in (schema.get("properties") or {})


def test_widget_metadata_mcp_app_mode():
    tools = asyncio.run(_list_tools_for_mode("mcp-app"))
    target = next(t for t in tools if t.name == "widget_tool")
    meta = _tool_meta(target)
    uri = "ui://widget/my-custom-widget-route.html"
    assert meta.get("ui/template") == uri
    assert meta.get("ui") == {"resourceUri": uri, "visibility": "visible"}
    assert "openai/outputTemplate" not in meta


def test_widget_metadata_object_form_universal():
    tools = asyncio.run(_list_tools_for_mode("universal"))
    target = next(t for t in tools if t.name == "widget_tool_object")
    meta = _tool_meta(target)
    uri = "ui://widget/object-route.html"
    assert meta.get("openai/outputTemplate") == uri
    assert meta["ui"]["resourceUri"] == uri
    assert meta["ui"]["prefersBorder"] is True


def test_widget_resource_uri_normalization():
    assert widget_resource_uri("calculator-result") == "ui://widget/calculator-result.html"
    assert widget_resource_uri("ui://widget/pizza-map.html") == "ui://widget/pizza-map.html"
    try:
        widget_resource_uri("")
        raise AssertionError("empty route should raise")
    except ValueError:
        pass


if __name__ == "__main__":
    test_widget_resource_uri_normalization()
    test_widget_metadata_openai_mode()
    test_widget_metadata_mcp_app_mode()
    test_widget_metadata_object_form_universal()
    print("widget metadata tests passed.")
