"""Regression: tools/resources/prompts list order must not depend on PYTHONHASHSEED."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

_WORKER = textwrap.dedent(
    """
    import asyncio
    import json
    import os

    os.environ.setdefault("DO_NOT_TRACK", "1")

    import mcp.types as types
    from pydantic import BaseModel, Field

    from nitrostack import DIContainer, module, prompt, resource, tool
    from nitrostack.core.app import McpApplication

    class EchoIn(BaseModel):
        value: str = Field(default="x")

    def _provider(mod_idx: int) -> type:
        attrs = {}
        for ti in range(3):
            name = f"tool_{mod_idx}_{ti}"

            def _make_tool(tool_name: str):
                @tool(name=tool_name, description="d", input_schema=EchoIn)
                async def handler(self, input: EchoIn) -> str:
                    return input.value

                return handler

            attrs[f"_tool_{ti}"] = _make_tool(name)
        return type(f"P{mod_idx}", (), attrs)

    mods = []
    for mi in range(3):
        prov = _provider(mi)

        @module(name=f"sub-{mi}", providers=[prov])
        class Sub:
            pass

        mods.append(Sub)

    root_attrs = {}
    for ri in range(2):
        uri = f"bench://r{ri}"

        def _make_res(resource_uri: str, idx: int):
            @resource(uri=resource_uri, name=f"res{idx}", description="d")
            async def handler(self) -> str:
                return "ok"

            return handler

        root_attrs[f"_res_{ri}"] = _make_res(uri, ri)

    for pi in range(2):
        pname = f"prompt_{pi}"

        @prompt(name=pname, description="d")
        async def ph(self):
            return []

        root_attrs[f"_prompt_{pi}"] = ph

    RootBase = type("RootBase", (), root_attrs)

    @module(name="root", imports=mods, providers=[RootBase])
    class Root:
        pass

    DIContainer.reset()
    app = McpApplication(Root)
    srv = app.mcp_server

    async def listed():
        tools = (
            await srv.request_handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )
        ).root.tools
        resources = (
            await srv.request_handlers[types.ListResourcesRequest](
                types.ListResourcesRequest(method="resources/list")
            )
        ).root.resources
        prompts = (
            await srv.request_handlers[types.ListPromptsRequest](
                types.ListPromptsRequest(method="prompts/list")
            )
        ).root.prompts
        return {
            "tools": [t.name for t in tools],
            "resources": [r.uri for r in resources],
            "prompts": [p.name for p in prompts],
        }

    print(json.dumps(asyncio.run(listed())))
    """
)


def _capture_lists_with_hash_seed(seed: str) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": seed, "DO_NOT_TRACK": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", _WORKER],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"worker failed (seed={seed}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return json.loads(proc.stdout.strip())


def test_list_endpoints_order_independent_of_hash_seed():
    results = [_capture_lists_with_hash_seed(s) for s in ("0", "1", "2")]
    assert results[0] == results[1] == results[2]


def test_multi_module_tools_follow_import_declaration_order():
    names = _capture_lists_with_hash_seed("0")["tools"]
    assert names == [
        "tool_0_0",
        "tool_0_1",
        "tool_0_2",
        "tool_1_0",
        "tool_1_1",
        "tool_1_2",
        "tool_2_0",
        "tool_2_1",
        "tool_2_2",
    ]