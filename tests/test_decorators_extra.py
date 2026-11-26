
"""Phase 6 — cache, rate_limit, and health_check decorators."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.core.additional_decorators import (
    copy_mcp_attributes,
    HealthCheckRegistry,
    cache,
    health_check,
    rate_limit,
)
from nitrostack.core.context import ExecutionContext


def test_cache_expires_after_ttl(monkeypatch):
    calls = {"n": 0}

    class Svc:
        @cache(ttl=1)
        async def add(self, value: int) -> int:
            calls["n"] += 1
            return value

    svc = Svc()
    clock = {"now": 100.0}

    def fake_time():
        return clock["now"]

    monkeypatch.setattr("nitrostack.core.additional_decorators.time.time", fake_time)
    assert asyncio.run(svc.add(1)) == 1
    assert asyncio.run(svc.add(1)) == 1
    assert calls["n"] == 1
    clock["now"] = 102.0
    assert asyncio.run(svc.add(1)) == 1
    assert calls["n"] == 2


def test_cache_returns_same_result_within_ttl():
    calls = {"n": 0}

    class Svc:
        @cache(ttl=60)
        async def add(
            self,
            value: int,
            context: ExecutionContext | None = None,
        ) -> int:
            calls["n"] += 1
            return value + 1

    svc = Svc()
    first = asyncio.run(svc.add(2))
    second = asyncio.run(svc.add(2))
    assert first == second == 3
    assert calls["n"] == 1


def test_rate_limit_raises_after_max():
    class Svc:
        @rate_limit(max=2, window=60)
        async def ping(self) -> str:
            return "ok"

    svc = Svc()
    assert asyncio.run(svc.ping()) == "ok"
    assert asyncio.run(svc.ping()) == "ok"
    try:
        asyncio.run(svc.ping())
        raise AssertionError("rate limit should fire")
    except ValueError as exc:
        assert "Rate limit exceeded" in str(exc)


def test_health_check_registry_reports_status():
    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()

    class Probe:
        @health_check("db")
        def db_ok(self) -> bool:
            return True

    HealthCheckRegistry.bind_instance("db", Probe.db_ok, Probe())
    results = asyncio.run(HealthCheckRegistry.run_all())
    assert results["db"] == "healthy"

    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()


def test_health_check_registry_records_errors():
    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()

    def boom():
        raise RuntimeError("down")

    HealthCheckRegistry._bound_checks["svc"] = boom
    results = asyncio.run(HealthCheckRegistry.run_all())
    assert results["svc"].startswith("error:")

    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()


def test_health_check_async_and_unhealthy():
    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()

    async def async_ok():
        return True

    def down():
        return False

    HealthCheckRegistry._bound_checks["async"] = async_ok
    HealthCheckRegistry._bound_checks["down"] = down
    results = asyncio.run(HealthCheckRegistry.run_all())
    assert results["async"] == "healthy"
    assert results["down"] == "unhealthy"

    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()


def test_copy_mcp_attributes_copies_mcp_prefixed_fields():
    def src():
        return None

    def dst():
        return None

    src._mcp_tool_config = {"name": "copied"}
    src._mcp_guards = ["G"]
    copy_mcp_attributes(src, dst)
    assert dst._mcp_tool_config == {"name": "copied"}
    assert dst._mcp_guards == ["G"]
