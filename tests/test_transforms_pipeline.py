import pytest

from nitrostack.transforms import (
    CatalogTransform, McpTransformBase, TransformContext, TransformError, TransformPipeline,
)


class Tag(McpTransformBase):
    def __init__(self, name, log):
        self.name = name
        self.log = log

    async def transform_tools(self, tools, ctx):
        self.log.append(self.name)
        return [t + f"+{self.name}" for t in tools]

    async def resolve_tool(self, name, next, ctx):
        self.log.append(f"resolve:{self.name}")
        found = await next(name)
        return None if found is None else f"{found}|{self.name}"


@pytest.mark.asyncio
async def test_empty_pipeline_is_identity():
    p = TransformPipeline([])
    assert not p.enabled
    tools = ["a", "b"]
    assert await p.transform_tools(tools, TransformContext()) == ["a", "b"]
    assert await p.resolve_tool("a", lambda n: n, TransformContext()) == "a"


@pytest.mark.asyncio
async def test_list_transforms_apply_in_declared_order():
    log = []
    p = TransformPipeline([Tag("one", log), Tag("two", log)])
    out = await p.transform_tools(["x"], TransformContext())
    assert out == ["x+one+two"]
    assert log == ["one", "two"]


@pytest.mark.asyncio
async def test_resolve_chains_through_next_first_is_outermost():
    log = []
    p = TransformPipeline([Tag("one", log), Tag("two", log)])
    out = await p.resolve_tool("x", lambda n: n, TransformContext())
    assert out == "x|two|one"
    assert log == ["resolve:one", "resolve:two"]


@pytest.mark.asyncio
async def test_resolve_unknown_tool_returns_none():
    p = TransformPipeline([Tag("one", [])])
    assert await p.resolve_tool("nope", lambda n: None, TransformContext()) is None


@pytest.mark.asyncio
async def test_raising_transform_becomes_transform_error():
    class Boom(McpTransformBase):
        name = "boom"

        async def transform_tools(self, tools, ctx):
            raise RuntimeError("bad")

    p = TransformPipeline([Boom()])
    with pytest.raises(TransformError) as e:
        await p.transform_tools(["a"], TransformContext())
    assert e.value.transform_name == "boom"
    # the pipeline is still usable afterwards
    assert await TransformPipeline([]).transform_tools(["a"], TransformContext()) == ["a"]


@pytest.mark.asyncio
async def test_dispose_runs_all_in_reverse_even_if_one_fails():
    order = []

    class D(McpTransformBase):
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        async def dispose(self):
            order.append(self.name)
            if self.fail:
                raise RuntimeError("x")

    p = TransformPipeline([D("a"), D("b", fail=True), D("c")])
    await p.dispose()
    assert order == ["c", "b", "a"]


def test_duplicate_names_rejected():
    with pytest.raises(ValueError):
        TransformPipeline([Tag("same", []), Tag("same", [])])


@pytest.mark.asyncio
async def test_catalog_transform_applies_and_supports_sync_apply():
    class Upper(CatalogTransform):
        name = "upper"

        def apply_transform(self, tools, ctx):
            return [t.upper() for t in tools]

    p = TransformPipeline([Upper()])
    assert await p.transform_tools(["a"], TransformContext()) == ["A"]