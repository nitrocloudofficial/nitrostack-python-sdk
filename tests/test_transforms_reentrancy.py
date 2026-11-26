import pytest

from nitrostack.transforms import (
    CatalogTransform, TransformContext, TransformPipeline, bypassing,
)


class Hide(CatalogTransform):
    name = "hide"

    def __init__(self):
        self.applied = 0

    def apply_transform(self, tools, ctx):
        self.applied += 1
        return [t for t in tools if t != "secret"]


@pytest.mark.asyncio
async def test_transform_is_skipped_while_its_own_call_is_running():
    h = Hide()
    p = TransformPipeline([h])
    ctx = TransformContext()

    assert await p.transform_tools(["a", "secret"], ctx) == ["a"]
    assert h.applied == 1

    with bypassing("hide"):
        assert await p.transform_tools(["a", "secret"], ctx) == ["a", "secret"]
    assert h.applied == 1  # not re-run

    assert await p.transform_tools(["a", "secret"], ctx) == ["a"]  # bypass was released
    assert h.applied == 2


@pytest.mark.asyncio
async def test_call_tool_with_caller_bypasses_that_transform_only():
    seen = {}

    async def invoker(name, args, ctx):
        from nitrostack.transforms import active_bypass
        seen["bypass"] = set(active_bypass())
        return f"called {name}"

    ctx = TransformContext(_invoker=invoker)
    assert await ctx.call_tool("real", {}, caller="hide") == "called real"
    assert seen["bypass"] == {"hide"}

    await ctx.call_tool("real", {})
    assert seen["bypass"] == set()