import asyncio

import pytest

from nitrostack import RateLimitExceededError, cache, rate_limit
from nitrostack.core import additional_decorators as deco


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(deco.time, "time", c)
    return c


def run(coro):
    return asyncio.run(coro)


def test_cache_evicts_least_recently_used(clock):
    calls = []

    @cache(ttl=60, max_size=2)
    async def f(x):
        calls.append(x)
        return x

    run(f(1))
    run(f(2))
    run(f(1))
    run(f(3))
    run(f(1))
    run(f(2))
    assert calls == [1, 2, 3, 2]


def test_cache_purges_expired_entries_before_lru(clock):
    calls = []

    @cache(ttl=10, max_size=2)
    async def f(x):
        calls.append(x)
        return x

    run(f(1))
    clock.now += 5
    run(f(2))
    clock.now += 6
    run(f(3))
    run(f(2))
    assert calls == [1, 2, 3]


def test_cache_unbounded_when_max_size_none(clock):
    calls = []

    @cache(ttl=60, max_size=None)
    async def f(x):
        calls.append(x)
        return x

    for i in range(2000):
        run(f(i))
    for i in range(2000):
        run(f(i))
    assert len(calls) == 2000


def test_cache_clear(clock):
    calls = []

    @cache(ttl=60)
    async def f(x):
        calls.append(x)
        return x

    run(f(1))
    run(f(1))
    f.cache_clear()
    run(f(1))
    assert calls == [1, 1]


def test_cache_rejects_invalid_max_size():
    with pytest.raises(ValueError):
        cache(ttl=60, max_size=0)


def test_rate_limit_error_is_typed_and_has_retry_after(clock):
    @rate_limit(max=2, window=60)
    async def ping():
        return "ok"

    run(ping())
    clock.now += 10
    run(ping())
    with pytest.raises(RateLimitExceededError) as info:
        run(ping())
    assert isinstance(info.value, ValueError)
    assert info.value.retry_after_seconds == pytest.approx(50)
    assert "Rate limit exceeded" in str(info.value)


def test_rate_limit_recovers_after_window(clock):
    @rate_limit(max=1, window=60)
    async def ping():
        return "ok"

    run(ping())
    with pytest.raises(RateLimitExceededError):
        run(ping())
    clock.now += 60
    assert run(ping()) == "ok"


def test_rate_limit_per_key(clock):
    @rate_limit(max=1, window=60, key=lambda user: user)
    async def ping(user):
        return user

    assert run(ping("a")) == "a"
    assert run(ping("b")) == "b"
    with pytest.raises(RateLimitExceededError):
        run(ping("a"))


def test_rate_limit_default_is_shared(clock):
    @rate_limit(max=1, window=60)
    async def ping(user):
        return user

    run(ping("a"))
    with pytest.raises(RateLimitExceededError):
        run(ping("b"))