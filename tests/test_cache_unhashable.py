from __future__ import annotations

import asyncio
import pytest

from pydantic import BaseModel

from nitrostack.core.additional_decorators import cache, _make_cache_key_part
from nitrostack.core.context import ExecutionContext


def run(coro):
    return asyncio.run(coro)


class SearchQuery(BaseModel):
    q: str
    limit: int = 10


class FilterOptions(BaseModel):
    tags: list[str] = []
    active: bool = True


class TestMakeCacheKeyPart:
    def test_pydantic_model_returns_deterministic_tuple(self):
        m = SearchQuery(q="hello", limit=5)
        part = _make_cache_key_part(m, "arg")
        assert isinstance(part, tuple)
        assert part[0] == "__pydantic__"
        assert part[1] == "SearchQuery"
        assert isinstance(part[2], str)

    def test_same_model_state_produces_same_key(self):
        a = SearchQuery(q="same", limit=3)
        b = SearchQuery(q="same", limit=3)
        assert _make_cache_key_part(a, "x") == _make_cache_key_part(b, "x")

    def test_different_model_state_produces_different_key(self):
        a = SearchQuery(q="foo")
        b = SearchQuery(q="bar")
        assert _make_cache_key_part(a, "x") != _make_cache_key_part(b, "x")

    def test_hashable_value_returned_as_is(self):
        assert _make_cache_key_part(42, "n") == 42
        assert _make_cache_key_part("text", "s") == "text"
        assert _make_cache_key_part(3.14, "f") == 3.14
        assert _make_cache_key_part(None, "n") is None
        assert _make_cache_key_part((1, 2), "t") == (1, 2)

    def test_unhashable_list_raises_descriptive_type_error(self):
        with pytest.raises(TypeError, match="args\\[0\\]"):
            _make_cache_key_part([1, 2, 3], "args[0]")

    def test_unhashable_dict_raises_descriptive_type_error(self):
        with pytest.raises(TypeError, match="my_param"):
            _make_cache_key_part({"a": 1}, "my_param")

    def test_error_message_contains_type_name(self):
        with pytest.raises(TypeError, match="list"):
            _make_cache_key_part([1], "x")

    def test_error_message_mentions_key_callable(self):
        with pytest.raises(TypeError, match="key="):
            _make_cache_key_part({"k": "v"}, "x")


class TestCacheWithPydanticModels:
    def test_pydantic_positional_arg_caches_correctly(self):
        calls = []

        @cache(ttl=60)
        async def search(query: SearchQuery) -> str:
            calls.append(query.q)
            return f"result:{query.q}"

        q = SearchQuery(q="python")
        assert run(search(q)) == "result:python"
        assert run(search(q)) == "result:python"
        assert calls == ["python"]

    def test_different_pydantic_models_produce_different_cache_entries(self):
        calls = []

        @cache(ttl=60)
        async def search(query: SearchQuery) -> str:
            calls.append(query.q)
            return query.q

        run(search(SearchQuery(q="a")))
        run(search(SearchQuery(q="b")))
        run(search(SearchQuery(q="a")))
        assert calls == ["a", "b"]

    def test_pydantic_keyword_arg_caches_correctly(self):
        calls = []

        @cache(ttl=60)
        async def fetch(query: SearchQuery, options: FilterOptions) -> int:
            calls.append(1)
            return len(query.q)

        q = SearchQuery(q="hello")
        f = FilterOptions(tags=["python"])
        assert run(fetch(query=q, options=f)) == 5
        assert run(fetch(query=q, options=f)) == 5
        assert calls == [1]

    def test_pydantic_model_cache_isolation_between_decorated_functions(self):
        calls_a, calls_b = [], []

        @cache(ttl=60)
        async def fn_a(q: SearchQuery) -> str:
            calls_a.append(q.q)
            return "a"

        @cache(ttl=60)
        async def fn_b(q: SearchQuery) -> str:
            calls_b.append(q.q)
            return "b"

        q = SearchQuery(q="x")
        run(fn_a(q))
        run(fn_b(q))
        assert calls_a == ["x"]
        assert calls_b == ["x"]

    def test_pydantic_positional_and_keyword_mixed(self):
        calls = []

        @cache(ttl=60)
        async def op(query: SearchQuery, limit: int = 5) -> str:
            calls.append((query.q, limit))
            return f"{query.q}:{limit}"

        q = SearchQuery(q="mix")
        run(op(q, limit=3))
        run(op(q, limit=3))
        assert calls == [("mix", 3)]

        run(op(q, limit=10))
        assert len(calls) == 2


class TestCacheWithUnhashableArguments:
    def test_unhashable_positional_arg_raises_type_error(self):
        @cache(ttl=60)
        async def fn(data: list) -> int:
            return len(data)

        with pytest.raises(TypeError, match="args\\[0\\]"):
            run(fn([1, 2, 3]))

    def test_unhashable_keyword_arg_raises_type_error(self):
        @cache(ttl=60)
        async def fn(mapping: dict) -> int:
            return len(mapping)

        with pytest.raises(TypeError, match="mapping"):
            run(fn(mapping={"a": 1}))

    def test_error_identifies_unhashable_type(self):
        @cache(ttl=60)
        async def fn(x: set) -> int:
            return len(x)

        with pytest.raises(TypeError, match="set"):
            run(fn({1, 2}))


class TestCacheCustomKeyCallable:
    def test_custom_key_accepts_pydantic_model(self):
        calls = []

        @cache(ttl=60, key=lambda q: q.q)
        async def search(query: SearchQuery) -> str:
            calls.append(query.q)
            return query.q

        run(search(SearchQuery(q="hi")))
        run(search(SearchQuery(q="hi")))
        assert calls == ["hi"]

    def test_custom_key_discriminates_on_custom_field(self):
        calls = []

        @cache(ttl=60, key=lambda q: q.q)
        async def search(query: SearchQuery) -> str:
            calls.append(query.q)
            return query.q

        run(search(SearchQuery(q="same", limit=1)))
        run(search(SearchQuery(q="same", limit=99)))
        assert calls == ["same"]

    def test_custom_key_accepts_unhashable_list(self):
        calls = []

        @cache(ttl=60, key=lambda data: tuple(sorted(data)))
        async def process(data: list) -> int:
            calls.append(len(data))
            return len(data)

        run(process([3, 1, 2]))
        run(process([3, 1, 2]))
        assert calls == [3]

    def test_custom_key_with_kwargs(self):
        calls = []

        @cache(ttl=60, key=lambda *, q, limit: (q, limit))
        async def search(*, q: str, limit: int) -> str:
            calls.append((q, limit))
            return f"{q}:{limit}"

        run(search(q="hello", limit=5))
        run(search(q="hello", limit=5))
        assert calls == [("hello", 5)]

    def test_custom_key_function_called_with_filtered_args(self):
        seen = []

        def my_key(*args, **kwargs):
            seen.append((args, kwargs))
            return str(args) + str(sorted(kwargs.items()))

        @cache(ttl=60, key=my_key)
        async def fn(value: int, ctx: ExecutionContext | None = None) -> int:
            return value

        ctx = ExecutionContext.__new__(ExecutionContext)
        run(fn(42, ctx=ctx))

        assert all(
            not any(isinstance(a, ExecutionContext) for a in call_args)
            for call_args, _ in seen
        )
        assert all(
            not any(isinstance(v, ExecutionContext) for v in call_kwargs.values())
            for _, call_kwargs in seen
        )
