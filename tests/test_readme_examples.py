"""README examples must stay true to the SDK: PyPI shows this README.

Every ```python block must compile and import only names the SDK has. The
Quick Start and Building Blocks examples are also run through the testing
harness.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from nitrostack import DIContainer, NitroTestingModule, module  # noqa: E402
from nitrostack.core.additional_decorators import HealthCheckRegistry  # noqa: E402
from nitrostack.events.event_emitter import EventEmitter  # noqa: E402

README = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
FENCE = re.compile(r"^```python\n(.*?)^```", re.S | re.M)


def _section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert match, f"README has no '## {title}' section"
    return match.group(1)


def _blocks(text: str):
    return FENCE.findall(text)


@pytest.fixture(autouse=True)
def fresh_globals():
    DIContainer.reset()
    EventEmitter.reset()
    HealthCheckRegistry._bound_checks.clear()
    yield
    DIContainer.reset()
    EventEmitter.reset()
    HealthCheckRegistry._bound_checks.clear()


def test_every_python_block_compiles():
    blocks = _blocks(README)
    assert len(blocks) >= 10
    for i, code in enumerate(blocks):
        compile(code, f"README.md python block {i + 1}", "exec")


def test_every_nitrostack_import_exists():
    imports = re.compile(r"^from (nitrostack[\w.]*) import (\([^)]*\)|[^\n]*)", re.M)
    checked = 0
    for code in _blocks(README):
        for mod_name, names in imports.findall(code):
            mod = importlib.import_module(mod_name)
            for name in re.split(r"[,\s()]+", names):
                if not name or name.startswith("#"):
                    continue
                assert hasattr(mod, name), f"README imports {name!r} from {mod_name}, which doesn't have it"
                checked += 1
    assert checked >= 30


def test_quick_start_runs():
    code = _blocks(_section("Quick Start"))[0]
    app = types.ModuleType("readme_quick_start_app")
    exec(compile(code, "README quick start", "exec"), app.__dict__)

    async def run():
        harness = await NitroTestingModule.create(app.AppModule)
        assert await harness.call_tool("add", {"a": 5, "b": 10}) == 15.0
        assert await harness.read_resource("calc://info") == "Simple Add Calculator v1.0.0"

    asyncio.run(run())


def test_building_blocks_run(monkeypatch):
    monkeypatch.setenv("WEATHER_API_KEY", "test")
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.delenv("OAUTH_REQUIRED", raising=False)

    # The section's blocks build on each other; keep every controller and
    # service each block defines (several reuse the name WeatherController).
    namespace: dict = {"__name__": "readme_building_blocks"}
    controllers, providers = [], []
    for i, code in enumerate(_blocks(_section("Building Blocks"))):
        before = dict(namespace)
        exec(compile(code, f"README building block {i + 1}", "exec"), namespace)
        for name, value in namespace.items():
            if not isinstance(value, type) or before.get(name) is value:
                continue
            if name.endswith("Controller"):
                controllers.append(value)
            elif name in ("WeatherService", "OrderService", "AuditLog"):
                providers.append(value)

    @module(name="app", imports=[namespace["ApiKeyModule"].for_root()], controllers=controllers, providers=providers)
    class AppModule:
        pass

    async def run():
        h = await NitroTestingModule.create(AppModule)
        assert await h.call_tool("get_weather", {"city": "Paris"}) == {"city": "Paris", "temp": 21, "units": "metric"}
        assert await h.call_tool("whats_new", {}) == "Try get_weather."
        assert await h.read_resource("weather://units") == "metric"
        messages = await h.get_prompt("forecast_brief", {"city": "Oslo"})
        assert "Oslo" in str(messages)

        assert await h.call_tool("get_forecast", {"city": "Rome"}) == {"city": "Rome", "forecast": "sunny"}
        assert "Access denied" in str(await h.call_tool("get_balance", {}))
        assert "Not authenticated" in str(await h.call_tool("close_account", {}))

        assert [await h.call_tool("ping", {}) for _ in range(10)] == ["pong"] * 10
        assert "Rate limit exceeded" in str(await h.call_tool("ping", {}))

        service = DIContainer.get_instance().resolve(namespace["WeatherService"])
        assert await service.lookup("Paris") is await service.lookup("Paris")

        assert await h.call_tool("build_report", {"steps": 1}) == {"steps": 1}

        await DIContainer.get_instance().resolve(namespace["OrderService"]).place("A1")
        assert DIContainer.get_instance().resolve(namespace["AuditLog"]).entries == ["A1"]
        assert await h.read_resource("health://status") == {"audit": "healthy"}

    asyncio.run(run())


def test_config_service_example(tmp_path, monkeypatch):
    code = _blocks(_section("Quick Start"))[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEATHER_API_KEY", "k")
    monkeypatch.delenv("WEATHER_UNITS", raising=False)
    namespace: dict = {"__name__": "readme_config"}
    exec(compile(code, "README config example", "exec"), namespace)

    async def run():
        await NitroTestingModule.create(namespace["AppModule"])
        service = DIContainer.get_instance().resolve(namespace["WeatherService"])
        assert (service.units, service.api_key) == ("metric", "k")

    asyncio.run(run())
