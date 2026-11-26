import asyncio
import json
import threading
import warnings

import pytest

from nitrostack import injectable, module
from nitrostack.core.additional_decorators import HealthCheckRegistry, health_check
from nitrostack.testing import NitroTestingModule


@pytest.fixture(autouse=True)
def clean_registry():
    saved = (dict(HealthCheckRegistry._checks), dict(HealthCheckRegistry._bound_checks))
    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()
    yield
    HealthCheckRegistry._checks.clear()
    HealthCheckRegistry._bound_checks.clear()
    HealthCheckRegistry._checks.update(saved[0])
    HealthCheckRegistry._bound_checks.update(saved[1])


def _app_module():
    @injectable()
    class Svc:
        @health_check("async_ok")
        async def async_ok(self):
            await asyncio.sleep(0)
            return True

        @health_check("sync_ok")
        def sync_ok(self):
            return True

        @health_check("async_boom")
        async def async_boom(self):
            raise RuntimeError("db down")

    @module(name="m", providers=[Svc])
    class M:
        pass

    return M


def _read_status():
    async def run():
        harness = await NitroTestingModule.create(_app_module())
        raw = await harness.read_resource("health://status")
        return json.loads(raw) if isinstance(raw, str) else raw

    return asyncio.run(run())


def test_async_and_sync_checks_both_report_healthy():
    status = _read_status()
    assert "event loop" not in str(status["async_ok"])
    assert status["async_ok"] == status["sync_ok"]


def test_raising_check_reports_error_without_affecting_others():
    status = _read_status()
    assert "db down" in status["async_boom"]
    assert "error" not in str(status["async_ok"])
    assert "error" not in str(status["sync_ok"])


def test_no_never_awaited_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        _read_status()


def _read_checks(app_module):
    async def run():
        harness = await NitroTestingModule.create(app_module)
        return await harness.read_resource("health://checks")

    return asyncio.run(run())


def test_health_payload_distinguishes_healthy_degraded_and_failing():
    @injectable()
    class Svc:
        @health_check("healthy")
        def healthy(self):
            return True

        @health_check("degraded")
        def degraded(self):
            return False

        @health_check("failing")
        def failing(self):
            raise RuntimeError("dependency unavailable")

    @module(name="health-statuses", providers=[Svc])
    class M:
        pass

    payload = _read_checks(M)
    statuses = {check["name"]: check["status"] for check in payload["checks"]}
    assert statuses == {"healthy": "healthy", "degraded": "unhealthy", "failing": "error"}
    failing_check = next(check for check in payload["checks"] if check["name"] == "failing")
    assert failing_check["message"] == "Check failed"
    assert payload["status"] == "unhealthy"


def test_false_check_degrades_aggregate_without_failing_it():
    @injectable()
    class Svc:
        @health_check("partial")
        def partial(self):
            return False

    @module(name="health-degraded", providers=[Svc])
    class M:
        pass

    payload = _read_checks(M)
    assert payload["status"] == "degraded"


def test_timed_out_sync_check_does_not_block_asyncio_run_or_other_checks():
    release = threading.Event()

    @injectable()
    class Svc:
        @health_check("slow_sync", timeout=0.05)
        def slow_sync(self):
            release.wait(timeout=5)
            return True

        @health_check("fast")
        async def fast(self):
            await asyncio.sleep(0.005)
            return True

    @module(name="health-timeouts", providers=[Svc])
    class M:
        pass

    result = {}
    read_complete = threading.Event()

    def read_resource():
        try:
            result["payload"] = _read_checks(M)
        finally:
            read_complete.set()

    reader = threading.Thread(target=read_resource)
    reader.start()
    completed_before_release = read_complete.wait(timeout=2)
    release.set()
    reader.join(timeout=2)

    assert completed_before_release, "asyncio.run waited for the timed-out sync check"
    assert not reader.is_alive()
    payload = result["payload"]
    statuses = {check["name"]: check for check in payload["checks"]}
    assert statuses["slow_sync"]["status"] == "timeout"
    assert statuses["slow_sync"]["message"] == "Timed out after 0.05s"
    assert statuses["fast"]["status"] == "healthy"
    assert payload["status"] == "unhealthy"


def test_health_status_retains_legacy_string_map():
    status = _read_status()
    assert status["async_ok"] == "healthy"
    assert status["sync_ok"] == "healthy"
    assert status["async_boom"] == "error: db down"
