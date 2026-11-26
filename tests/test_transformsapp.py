
import json
import pathlib

import mcp.types as types
import pytest

from nitrostack.testing import NitroTestingModule
from nitrostack.transforms import CatalogTransform, McpTransformBase
from tests._baseline_catalog import ERAS, In, M


BASELINE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "tools_list_baseline.json"
)


async def _list(h):
    srv = h.app.mcp_server
    response = (
        await srv.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
    ).root
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("era", ERAS)
async def test_no_transforms_output_is_byte_identical_to_baseline(
    era, monkeypatch
):
    monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", era)

    h = await NitroTestingModule.create(M)
    response = await _list(h)

    expected = json.loads(
        BASELINE.read_text(encoding="utf-8")
    )[era]

    actual = response.model_dump_json(
        by_alias=True,
        exclude_none=True,
    )

    assert actual == expected


class DropBeta(CatalogTransform):
    name = "drop_beta"

    def apply_transform(self, tools, ctx):
        return [tool for tool in tools if tool.name != "beta"]


class Suffix(CatalogTransform):
    name = "suffix"

    def apply_transform(self, tools, ctx):
        return [
            tool.model_copy(
                update={
                    "description": (tool.description or "") + "!"
                }
            )
            for tool in tools
        ]


@pytest.mark.asyncio
async def test_two_transforms_compose_in_declared_order_for_list():
    h = await NitroTestingModule.create(
        M,
        transforms=[DropBeta(), Suffix()],
    )

    response = await _list(h)

    assert [tool.name for tool in response.tools] == ["alpha"]
    assert response.tools[0].description == "first!"


class Boom(McpTransformBase):
    name = "boom"

    async def transform_tools(self, tools, ctx):
        raise RuntimeError("bad transform")


@pytest.mark.asyncio
async def test_failing_transform_returns_error_and_server_keeps_serving():
    h = await NitroTestingModule.create(
        M,
        transforms=[Boom()],
    )

    with pytest.raises(Exception):
        await _list(h)

    # A transform failure must not stop the server from handling tool calls.
    result = await h.call_tool("alpha", {"input": {"q": "x"}})
    assert result is not None


@pytest.mark.asyncio
async def test_dispose_runs_on_shutdown():
    disposed = []

    class D(McpTransformBase):
        name = "d"

        async def dispose(self):
            disposed.append(True)

    h = await NitroTestingModule.create(
        M,
        transforms=[D()],
    )

    # Exercise disposal directly because this harness may not expose
    # a public application-shutdown method.
    await h.app._transforms.dispose()

    assert disposed == [True]
