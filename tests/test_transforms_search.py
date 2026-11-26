import asyncio
import time

import mcp.types as types
import pytest

from nitrostack.transforms import TransformContext
from nitrostack.transforms.search import (
    CallToolInput, RegexSearchTransform, SearchToolsInput, is_browse_query,
)
from nitrostack.transforms.validation import ToolArgumentError


def run(coro):
    return asyncio.run(coro)


def make_tool(name, description="", props=None, meta=None):
    schema = {"type": "object", "properties": props or {}}
    return types.Tool(name=name, description=description, inputSchema=schema, _meta=meta)


class FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return list(self._tools)


class FakeCtx(TransformContext):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def call_tool(self, name, arguments=None, *, caller=None):
        self.calls.append((name, arguments, caller))
        return {"ok": True}


def registered(transform, tools):
    transform.on_register(FakeRegistry(tools))
    return transform


async def search(transform, **kw):
    ctx = TransformContext()
    tool = await transform.resolve_tool(transform.search_tool_name, _none, ctx)
    return await tool.handler(SearchToolsInput(**kw), ctx)


async def _none(name):
    return None


CATALOG = [
    make_tool("get_user", "Look up a user by id. Returns a profile.", {"user_id": {"type": "string", "description": "the id"}}),
    make_tool("get_order", "Fetch an order.", {"order_id": {"type": "string"}}),
    make_tool("send_email", "Send an email message.", {"to": {"type": "string"}}),
]


# ---- tools/list -------------------------------------------------------------

def test_list_shows_only_meta_tools_and_always_visible():
    t = RegexSearchTransform(always_visible=["send_email"])
    out = run(t.transform_tools(CATALOG, TransformContext()))
    assert [x.name for x in out] == ["search_tools", "call_tool", "send_email"]


def test_meta_flag_keeps_tool_visible():
    flagged = make_tool("pinned", "x", meta={"alwaysVisible": True})
    t = RegexSearchTransform()
    out = run(t.transform_tools([flagged, *CATALOG], TransformContext()))
    assert "pinned" in [x.name for x in out]


def test_tool_names_are_configurable():
    t = RegexSearchTransform(search_tool_name="find", call_tool_name="run")
    out = run(t.transform_tools(CATALOG, TransformContext()))
    assert [x.name for x in out] == ["find", "run"]


def test_same_meta_names_rejected():
    with pytest.raises(ValueError):
        RegexSearchTransform(search_tool_name="x", call_tool_name="x")


def test_search_tool_is_read_only():
    t = RegexSearchTransform()
    out = run(t.transform_tools(CATALOG, TransformContext()))
    assert out[0].annotations.read_only_hint is True
    # and it serializes to the MCP wire name
    assert out[0].model_dump(by_alias=True, exclude_none=True)["annotations"]["readOnlyHint"] is True

# ---- browse -----------------------------------------------------------------

@pytest.mark.parametrize("q", ["", "   ", "show tools", "list all tools", "what tools do you have"])
def test_browse_queries(q):
    assert is_browse_query(q)


@pytest.mark.parametrize("q", ["email", "get user", "(a+)+$", "^get_"])
def test_task_queries_are_not_browse(q):
    assert not is_browse_query(q)


def test_browse_returns_brief_index_of_whole_catalog():
    t = registered(RegexSearchTransform(), CATALOG)
    out = run(search(t, query="show tools"))
    assert out["mode"] == "browse"
    assert [r["name"] for r in out["results"]] == ["get_order", "get_user", "send_email"]
    assert set(out["results"][0]) == {"name", "summary"}


# ---- search -----------------------------------------------------------------

def test_literal_match_is_case_insensitive_and_ranks_name_first():
    t = registered(RegexSearchTransform(), CATALOG)
    out = run(search(t, query="USER"))
    assert [r["name"] for r in out["results"]] == ["get_user"]


def test_no_match_returns_empty_list_not_error():
    t = registered(RegexSearchTransform(), CATALOG)
    out = run(search(t, query="zzzzz"))
    assert out["results"] == []
    assert "No tools matched" in out["note"]


def test_limit_above_20_is_clamped():
    many = [make_tool(f"t{i:02d}", f"item {i}") for i in range(25)]
    t = registered(RegexSearchTransform(), many)
    out = run(search(t, query="item", limit=500))
    assert len(out["results"]) == 20
    assert "reduced" in out["note"]


def test_limit_is_respected_below_cap():
    many = [make_tool(f"t{i:02d}", f"item {i}") for i in range(25)]
    t = registered(RegexSearchTransform(), many)
    assert len(run(search(t, query="item", limit=3))["results"]) == 3


def test_equal_scores_sort_by_name():
    tools = [make_tool("b_tool", "widget"), make_tool("a_tool", "widget")]
    t = registered(RegexSearchTransform(), tools)
    out = run(search(t, query="widget"))
    assert [r["name"] for r in out["results"]] == ["a_tool", "b_tool"]


def test_detail_levels():
    t = registered(RegexSearchTransform(), CATALOG)
    brief = run(search(t, query="user", detail="brief"))["results"]
    detailed = run(search(t, query="user", detail="detailed"))["results"]
    full = run(search(t, query="user", detail="full"))["results"]

    assert [r["name"] for r in detailed] == ["get_user"]
    assert set(brief[0]) == {"name", "summary"}
    assert detailed[0]["parameters"][0]["name"] == "user_id"
    assert detailed[0]["parameters"][0]["required"] is False
    assert "inputSchema" in full[0]


# ---- regex ------------------------------------------------------------------

def test_literal_by_default_treats_dot_literally():
    t = registered(RegexSearchTransform(), [make_tool("getXuser", "x")])
    assert run(search(t, query="get.user"))["results"] == []


def test_regex_enabled_matches_pattern():
    t = registered(RegexSearchTransform(allow_regex=True), CATALOG)
    out = run(search(t, query="^get_"))
    assert [r["name"] for r in out["results"]] == ["get_order", "get_user"]


def test_invalid_regex_falls_back_to_literal():
    t = registered(RegexSearchTransform(allow_regex=True), CATALOG)
    out = run(search(t, query="(["))
    assert out["results"] == []
    assert "literal" in out["note"]


def test_catastrophic_pattern_returns_within_timeout_using_literal():
    nasty = make_tool("victim", "a" * 40 + "!")
    t = registered(RegexSearchTransform(allow_regex=True, regex_timeout=1.0), [nasty, *CATALOG])
    start = time.monotonic()
    out = run(search(t, query="(a+)+$"))
    assert time.monotonic() - start < 8
    assert "timed out" in out["note"]
    assert out["results"] == []  # literal "(a+)+$" matches nothing


def test_long_pattern_is_not_run_as_regex():
    t = registered(RegexSearchTransform(allow_regex=True, max_pattern_length=10), CATALOG)
    out = run(search(t, query="x" * 50))
    assert "longer than" in out["note"]


# ---- call_tool --------------------------------------------------------------

def test_call_tool_dispatches_through_context_skipping_this_transform():
    t = registered(RegexSearchTransform(), CATALOG)
    ctx = FakeCtx()
    tool = run(t.resolve_tool("call_tool", _none, ctx))
    run(tool.handler(CallToolInput(name="get_user", arguments={"user_id": "1"}), ctx))
    assert ctx.calls == [("get_user", {"user_id": "1"}, "regex_search")]


@pytest.mark.parametrize("target", ["search_tools", "call_tool"])
def test_call_tool_rejects_meta_tools(target):
    t = registered(RegexSearchTransform(), CATALOG)
    ctx = FakeCtx()
    tool = run(t.resolve_tool("call_tool", _none, ctx))
    with pytest.raises(ToolArgumentError):
        run(tool.handler(CallToolInput(name=target), ctx))
    assert ctx.calls == []


def test_unknown_names_fall_through_to_next_resolver():
    t = RegexSearchTransform()

    async def nxt(name):
        return f"real:{name}"

    assert run(t.resolve_tool("get_user", nxt, TransformContext())) == "real:get_user"