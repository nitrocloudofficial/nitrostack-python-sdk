import asyncio

import mcp.types as types
from pydantic import BaseModel

from nitrostack import ExecutionContext, injectable, module, tool
from nitrostack.testing import NitroTestingModule
from nitrostack.transforms.search import RegexSearchTransform
from nitrostack import use_guards

class In(BaseModel):
    q: str = ""


@injectable()
class C:
    @tool(name="alpha", description="first tool about apples", input_schema=In)
    async def alpha(self, input: In, context: ExecutionContext) -> str:
        return "alpha-result"

    @tool(name="beta", description="second tool about bananas", input_schema=In)
    async def beta(self, input: In, context: ExecutionContext) -> str:
        return "beta-result"


@module(name="m", controllers=[C])
class M:
    pass


def run(coro):
    return asyncio.run(coro)


async def _list(h):
    srv = h.app.mcp_server
    return (await srv.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list"))).root


def test_tools_list_returns_only_meta_tools():
    async def go():
        h = await NitroTestingModule.create(M, transforms=[RegexSearchTransform()])
        return [t.name for t in (await _list(h)).tools]

    assert run(go()) == ["search_tools", "call_tool"]


def test_search_then_call_runs_the_real_tool():
    async def go():
        h = await NitroTestingModule.create(M, transforms=[RegexSearchTransform()])
        found = await h.call_tool("search_tools", {"input": {"query": "bananas"}})
        called = await h.call_tool("call_tool", {"input": {"name": "beta", "arguments": {"q": "x"}}})
        return found, called

    found, called = run(go())
    assert "beta" in str(found)
    assert "beta-result" in str(called)



def test_call_tool_cannot_call_itself():
    async def go():
        h = await NitroTestingModule.create(
            M, transforms=[RegexSearchTransform()]
        )
        return await h.call_tool(
            "call_tool",
            {"input": {"name": "call_tool"}},
        )

    result = run(go())
    assert "cannot be called" in str(result)
