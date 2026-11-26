import asyncio
import math

import mcp.types as types
import pytest

from nitrostack.transforms import TransformContext
from nitrostack.transforms.search import (
    Bm25Engine, Bm25SearchTransform, SearchToolsInput, tokenize,
)


def run(coro):
    return asyncio.run(coro)


def make_tool(name, description="", props=None):
    return types.Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": props or {}},
    )


CATALOG = [
    make_tool("get_user", "Look up a user profile by id", {"user_id": {"type": "string"}}),
    make_tool("get_order", "Fetch an order and its line items", {"order_id": {"type": "string"}}),
    make_tool("send_email", "Send an email message to a recipient", {"to": {"type": "string"}}),
    make_tool("search_genes", "Look up a gene by symbol and return annotations", {"symbol": {"type": "string"}}),
    make_tool("get_rna_by_id", "Look up an RNA record by identifier", {"urs_id": {"type": "string"}}),
    make_tool("list_invoices", "List invoices for a customer", {}),
    make_tool("create_invoice", "Create an invoice for a customer order", {}),
    make_tool("delete_user", "Delete a user account", {}),
]


class FakeRegistry:
    def __init__(self, tools):
        self.tools = tools

    def list_tools(self):
        return list(self.tools)


def names(outcome):
    return [tool.name for tool, _score in outcome.ranked]


# ---- tokenizer -------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("getRnaById", ["get", "rna", "id"]),          # "by" is a stop word
    ("get_user_profile", ["get", "user", "profile"]),
    ("HTTPServer", ["http", "server"]),
    ("sha256Hash", ["sha256", "hash"]),            # digits stay attached
    ("find---genes!!", ["find", "genes"]),         # repeated separators
    ("Ünïcode Straße", ["ünïcode", "straße"]),
    ("", []),
    ("the of and", []),                            # stop words only
])
def test_tokenizer(text, expected):
    assert tokenize(text) == expected


def test_tokenizer_can_keep_stop_words():
    assert tokenize("the gene", drop_stop_words=False) == ["the", "gene"]


# ---- engine ----------------------------------------------------------------

def test_bm25_score_matches_hand_computation():
    engine = Bm25Engine(k1=1.5, b=0.75)
    engine.build([("A", [(1.0, ["apple", "banana"])]), ("B", [(1.0, ["banana"])])])
    # N=2, n=1: idf = ln(1 + 1.5/1.5) = ln 2.  avgdl = 1.5.  A: tf=1, dl=2.
    # denom = 1 + 1.5 * (0.25 + 0.75 * 2/1.5) = 2.875 ; num = 2.5
    expected = math.log(2) * 2.5 / 2.875
    result = engine.search(["apple"])
    assert [doc for doc, _ in result] == ["A"]
    assert result[0][1] == pytest.approx(expected)


def test_idf_is_never_negative_for_common_terms():
    engine = Bm25Engine()
    engine.build([(f"d{i}", [(1.0, ["common"])]) for i in range(5)])
    assert engine.idf("common") > 0


def test_equal_scores_sort_by_id():
    engine = Bm25Engine()
    engine.build([("b", [(1.0, ["x"])]), ("a", [(1.0, ["x"])])])
    assert [doc for doc, _ in engine.search(["x"])] == ["a", "b"]


def test_repeated_query_terms_count_once():
    engine = Bm25Engine()
    engine.build([("a", [(1.0, ["x", "y"])])])
    assert engine.search(["x", "x", "x"]) == engine.search(["x"])


@pytest.mark.parametrize("kwargs", [{"k1": -1}, {"b": 1.5}, {"b": -0.1}])
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        Bm25Engine(**kwargs)


# ---- ranking (golden) ------------------------------------------------------
# These expectations are reasoned, not generated. Run once, and if one fails,
# check the reasoning before changing the expected value.

def test_golden_single_match_queries():
    t = Bm25SearchTransform()
    assert names(run(t.rank("email", CATALOG))) == ["send_email"]
    assert names(run(t.rank("gene", CATALOG))) == ["search_genes"]   # via description
    assert names(run(t.rank("invoice", CATALOG))) == ["create_invoice"]
    assert names(run(t.rank("invoices", CATALOG))) == ["list_invoices"]  # no stemming


def test_golden_name_match_outranks_description_match():
    result = names(run(Bm25SearchTransform().rank("order", CATALOG)))
    assert result[0] == "get_order"
    assert "create_invoice" in result


def test_golden_user_query_returns_both_user_tools():
    result = names(run(Bm25SearchTransform().rank("user", CATALOG)))
    assert set(result[:2]) == {"get_user", "delete_user"}


def test_ranking_is_deterministic_across_instances_and_catalog_order():
    first = names(run(Bm25SearchTransform().rank("look up", CATALOG)))
    second = names(run(Bm25SearchTransform().rank("look up", list(reversed(CATALOG)))))
    assert first == second


def test_no_match_returns_empty_list_not_error():
    outcome = run(Bm25SearchTransform().rank("zzzzzz", CATALOG))
    assert outcome.ranked == []


def test_stop_word_only_query_explains_itself():
    outcome = run(Bm25SearchTransform().rank("of the", CATALOG))
    assert outcome.ranked == []
    assert "no searchable words" in outcome.note


# ---- index lifecycle -------------------------------------------------------

def test_index_is_not_rebuilt_on_every_search():
    t = Bm25SearchTransform()
    assert t.build_count == 0          # lazy
    for q in ("email", "gene", "order", "user"):
        run(t.rank(q, CATALOG))
    assert t.build_count == 1


def test_catalog_order_does_not_trigger_rebuild():
    t = Bm25SearchTransform()
    run(t.rank("email", CATALOG))
    run(t.rank("email", list(reversed(CATALOG))))
    assert t.build_count == 1


def test_adding_removing_or_editing_a_tool_rebuilds_once():
    t = Bm25SearchTransform()
    run(t.rank("email", CATALOG))

    added = CATALOG + [make_tool("send_sms", "Send a text message")]
    assert "send_sms" in names(run(t.rank("sms", added)))
    assert t.build_count == 2

    run(t.rank("sms", added))
    assert t.build_count == 2

    edited = [make_tool(x.name, "Totally new text") if x.name == "get_user" else x for x in added]
    run(t.rank("totally", edited))
    assert t.build_count == 3

    run(t.rank("email", [x for x in edited if x.name != "send_email"]))
    assert t.build_count == 4


# ---- through the search_tools handler --------------------------------------

async def _search(transform, **kw):
    ctx = TransformContext()

    async def none(_name):
        return None

    tool = await transform.resolve_tool(transform.search_tool_name, none, ctx)
    return await tool.handler(SearchToolsInput(**kw), ctx)


def test_search_tools_handler_returns_ranked_results_and_clamps_limit():
    t = Bm25SearchTransform()
    t.on_register(FakeRegistry(CATALOG))
    out = run(_search(t, query="email"))
    assert [r["name"] for r in out["results"]] == ["send_email"]
    big = run(_search(t, query="look", limit=500))
    assert len(big["results"]) <= 20
    assert "reduced" in big["note"]


def test_search_tools_handler_no_match_is_not_an_error():
    t = Bm25SearchTransform()
    t.on_register(FakeRegistry(CATALOG))
    out = run(_search(t, query="zzzzzz"))
    assert out["results"] == []
    assert "No tools matched" in out["note"]