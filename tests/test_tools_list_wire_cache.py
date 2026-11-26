"""Regression tests for tools/list schema caching (issue #20fffe0 follow-ups)."""

import json
import os
import pathlib
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from nitrostack import DataSpilloverInterceptor, module, tool, use_interceptors
from nitrostack.core.app import McpApplication
from nitrostack.testing import NitroTestingModule
from tests._baseline_catalog import M
from tests.test_transformsapp import _list

_BASELINE = pathlib.Path(__file__).parent / "fixtures" / "tools_list_baseline.json"


@pytest.mark.asyncio
async def test_tools_list_input_schema_isolated_between_calls():
    h = await NitroTestingModule.create(M)
    try:
        first = (await _list(h)).tools
        first[0].input_schema["wire_cache_probe"] = True
        second = (await _list(h)).tools
        assert "wire_cache_probe" not in second[0].input_schema
    finally:
        await h.close()


@pytest.mark.asyncio
async def test_tools_list_order_matches_baseline():
    h = await NitroTestingModule.create(M)
    try:
        with patch.dict(os.environ, {"NITRO_MCP_PROTOCOL_VERSION": "modern"}):
            response = await _list(h)
        names = [listed.name for listed in response.tools]
        expected = json.loads(_BASELINE.read_text(encoding="utf-8"))["modern"]
        baseline_names = [item["name"] for item in json.loads(expected)["tools"]]
        assert names == baseline_names
    finally:
        await h.close()


class _LateInput(BaseModel):
    value: str = "x"


def test_spillover_schema_when_tool_registered_after_bootstrap():
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    class Existing:
        @tool(name="existing", description="existing", input_schema=_LateInput)
        async def existing(self, input, context):
            return {"value": input.value}

    class Late:
        @tool(name="late_spill", description="late", input_schema=_LateInput, output_schema=schema)
        @use_interceptors(DataSpilloverInterceptor(threshold_bytes=1))
        async def late_spill(self, input, context):
            return {"value": input.value}

    @module(name="late-spill-root", providers=[Existing])
    class Root:
        pass

    app = McpApplication(Root)
    late = Late()
    tool_cfg = late.late_spill._mcp_tool_config
    app._register_tool(late, late.late_spill, tool_cfg)

    entry = app._tools["late_spill"]
    assert entry.has_spillover is None
    definition = app._build_tool_definition(entry)
    assert entry.has_spillover is True
    assert definition.output_schema["anyOf"][1]["required"] == [
        "preview",
        "resource_uri",
        "original_size_bytes",
    ]