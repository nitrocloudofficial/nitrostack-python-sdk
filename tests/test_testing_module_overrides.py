"""Issue #74: provider overrides, teardown and mocks in NitroTestingModule."""

from __future__ import annotations

import asyncio
import gc
import warnings
from unittest.mock import MagicMock

import pytest

from pydantic import BaseModel

from nitrostack import DIContainer, ExecutionContext, injectable, module, tool
from nitrostack.testing import MockLogger, NitroTestingModule, create_mock_context, flush_tasks

BUILT = []


class LookupInput(BaseModel):
    key: str


@injectable()
class Repo:
    def __init__(self):
        BUILT.append("real")

    def get(self, key):
        return f"real-{key}"


class FakeRepo:
    def __init__(self):
        self.destroyed = 0

    def get(self, key):
        return f"fake-{key}"

    def on_module_destroy(self):
        self.destroyed += 1


@injectable(deps=[Repo])
class Service:
    def __init__(self, repo):
        self.repo = repo
        self.destroyed = 0

    @tool(name="lookup", description="look up", input_schema=LookupInput)
    async def lookup(self, input: LookupInput, context: ExecutionContext) -> str:
        return self.repo.get(input.key)

    async def on_module_destroy(self):
        self.destroyed += 1


@module(name="svc", providers=[Repo, Service])
class AppModule:
    pass


@pytest.fixture(autouse=True)
def fresh():
    DIContainer.reset()
    BUILT.clear()
    yield
    DIContainer.reset()


@pytest.mark.asyncio
async def test_override_instance_is_used_and_original_never_built():
    fake = FakeRepo()
    app = await NitroTestingModule.create(AppModule, overrides={Repo: fake})
    assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "fake-a"
    assert BUILT == []
    await app.close()


@pytest.mark.asyncio
async def test_override_factory_sync_and_async():
    async def make():
        return FakeRepo()

    for override in (FakeRepo, make, lambda: FakeRepo()):
        DIContainer.reset()
        async with await NitroTestingModule.create(AppModule, overrides={Repo: override}) as app:
            assert await app.call_tool("lookup", {"input": {"key": "z"}}) == "fake-z"
    assert BUILT == []


@pytest.mark.asyncio
async def test_without_override_real_provider_used():
    async with await NitroTestingModule.create(AppModule) as app:
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "real-a"
    assert BUILT == ["real"]


@pytest.mark.asyncio
async def test_close_runs_hooks_once_even_when_called_twice():
    fake = FakeRepo()
    app = await NitroTestingModule.create(AppModule, overrides={Repo: fake})
    service = DIContainer.get_instance().resolve(Service)
    await app.close()
    await app.close()
    assert fake.destroyed == 1
    assert service.destroyed == 1


@pytest.mark.asyncio
async def test_async_with_closes_and_close_inside_is_idempotent():
    fake = FakeRepo()
    async with await NitroTestingModule.create(AppModule, overrides={Repo: fake}) as app:
        await app.close()
    assert fake.destroyed == 1


@pytest.mark.asyncio
async def test_close_restores_overrides_so_next_test_is_clean():
    async with await NitroTestingModule.create(AppModule, overrides={Repo: FakeRepo()}):
        pass
    assert not DIContainer.get_instance().has_value(Repo)


@pytest.mark.asyncio
async def test_failing_hook_does_not_stop_other_hooks():
    fake = FakeRepo()

    def boom():
        raise RuntimeError("hook failed")

    fake.on_module_destroy = boom
    app = await NitroTestingModule.create(AppModule, overrides={Repo: fake})
    service = DIContainer.get_instance().resolve(Service)
    with pytest.raises(RuntimeError, match="hook failed"):
        await app.close()
    assert service.destroyed == 1
    await app.close()  # already closed: no second raise


@pytest.mark.asyncio
async def test_magicmock_override_is_not_mistaken_for_a_tool():
    mock = MagicMock()
    mock.get.return_value = "mocked"
    async with await NitroTestingModule.create(AppModule, overrides={Repo: mock}) as app:
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "mocked"
    mock.on_module_destroy.assert_not_called()


@pytest.mark.asyncio
async def test_failed_create_restores_overrides():
    @injectable()
    class Boom:
        def __init__(self):
            raise RuntimeError("cannot build")

    @module(name="bad", providers=[Repo, Boom])
    class Bad:
        pass

    with pytest.raises(Exception):
        await NitroTestingModule.create(Bad, overrides={Repo: FakeRepo()})
    assert not DIContainer.get_instance().has_value(Repo)


def test_mock_logger():
    log = MockLogger()
    log.info("started up", {"a": 1})
    log.warn("careful")
    assert log.has_log("info", "started")
    assert log.has_log("warning", "careful")
    assert not log.has_log("error", "started")
    log.clear()
    assert not log.has_log("info", "started")


def test_create_mock_context():
    ctx = create_mock_context(tool_name="lookup", metadata={"k": 1})
    assert ctx.request_id == "test-request" and ctx.tool_name == "lookup"
    assert isinstance(ctx.logger, MockLogger)
    ctx.logger.error("bad thing")
    assert ctx.logger.has_log("error", "bad")


@pytest.mark.asyncio
async def test_flush_tasks_lets_background_work_finish():
    done = []

    async def inner():
        await asyncio.sleep(0)
        done.append(2)

    async def outer():
        asyncio.get_running_loop().create_task(inner())
        done.append(1)

    asyncio.get_running_loop().create_task(outer())
    await flush_tasks()
    assert done == [1, 2]


@pytest.mark.asyncio
async def test_no_resource_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        async with await NitroTestingModule.create(AppModule, overrides={Repo: FakeRepo()}) as app:
            await app.call_tool("lookup", {"input": {"key": "a"}})
        gc.collect()


@pytest.mark.nitro_app(module=AppModule)
@pytest.mark.asyncio
async def test_nitro_client_fixture(nitro_client):
    assert await nitro_client.call_tool("lookup", {"input": {"key": "q"}}) == "real-q"


@pytest.mark.asyncio
async def test_second_harness_does_not_reuse_service_holding_old_fake():
    first, second = FakeRepo(), FakeRepo()
    second.get = lambda key: f"second-{key}"
    async with await NitroTestingModule.create(AppModule, overrides={Repo: first}) as app:
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "fake-a"
    async with await NitroTestingModule.create(AppModule, overrides={Repo: second}) as app:
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "second-a"
    # And without overrides, nothing from the earlier harnesses is left behind.
    async with await NitroTestingModule.create(AppModule) as app:
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "real-a"


@pytest.mark.asyncio
async def test_teardown_order_covers_indirect_dependencies_consumers_first():
    order = []

    @injectable()
    class Leaf:
        def on_module_destroy(self):
            order.append("leaf")

    @injectable(deps=[Leaf])
    class Middle:
        def __init__(self, leaf):
            self.leaf = leaf

        def on_module_destroy(self):
            order.append("middle")

    @injectable(deps=[Middle])
    class Top:
        def __init__(self, middle):
            self.middle = middle

        def on_module_destroy(self):
            order.append("top")

    # Only Top is declared on the module; Leaf and Middle are reached through DI.
    @module(name="chain", providers=[Top])
    class Chain:
        pass

    async with await NitroTestingModule.create(Chain):
        pass
    assert order == ["top", "middle", "leaf"]


@pytest.mark.asyncio
async def test_failing_override_factory_rolls_back_earlier_overrides():
    def broken():
        raise RuntimeError("factory failed")

    before = dict(DIContainer.get_instance()._instances)
    with pytest.raises(RuntimeError, match="factory failed"):
        await NitroTestingModule.create(AppModule, overrides={Repo: FakeRepo(), "other": broken})
    assert DIContainer.get_instance()._instances == before
    assert not DIContainer.get_instance().has_value(Repo)


@pytest.fixture
def nitro_app():
    return AppModule


@pytest.mark.asyncio
async def test_nitro_client_resolves_nitro_app_fixture_dynamically(nitro_client):
    assert await nitro_client.call_tool("lookup", {"input": {"key": "f"}}) == "real-f"


@pytest.mark.asyncio
async def test_consumer_cached_before_create_is_rebuilt_with_the_override():
    container = DIContainer.get_instance()
    original_service = container.resolve(Service)  # built earlier, holds the real Repo
    original_repo = container.resolve(Repo)
    assert original_service.repo is original_repo
    fake = FakeRepo()
    async with await NitroTestingModule.create(AppModule, overrides={Repo: fake}) as app:
        # 1. The override is applied to the rebuilt consumer.
        assert await app.call_tool("lookup", {"input": {"key": "a"}}) == "fake-a"
        rebuilt = container.resolve(Service)
        assert rebuilt is not original_service
        assert rebuilt.repo is fake
    # 2. The rebuilt service and the override were shut down exactly once.
    assert rebuilt.destroyed == 1
    assert fake.destroyed == 1
    # 3. The originals are back by identity and were never shut down.
    assert container.resolve(Service) is original_service
    assert container.resolve(Repo) is original_repo
    assert original_service.destroyed == 0


@pytest.mark.asyncio
async def test_indirect_consumer_cached_before_create_is_rebuilt():
    order = []

    @injectable(deps=[Service])
    class Outer:
        def __init__(self, service):
            self.service = service
            self.destroyed = 0

        def on_module_destroy(self):
            self.destroyed += 1
            order.append("outer")

    container = DIContainer.get_instance()
    original_outer = container.resolve(Outer)
    original_service = original_outer.service
    original_repo = container.resolve(Repo)

    fake = FakeRepo()
    fake_destroy = fake.on_module_destroy
    fake.on_module_destroy = lambda: (order.append("fake"), fake_destroy())[1]
    service_destroy = Service.on_module_destroy

    async def tracked_destroy(self):
        order.append("service")
        await service_destroy(self)

    Service.on_module_destroy = tracked_destroy
    try:
        async with await NitroTestingModule.create(AppModule, overrides={Repo: fake}):
            # 1. The override reaches the indirect consumer.
            rebuilt_outer = container.resolve(Outer)
            rebuilt_service = rebuilt_outer.service
            assert rebuilt_outer is not original_outer
            assert rebuilt_service is not original_service
            assert rebuilt_outer.service.repo.get("x") == "fake-x"
    finally:
        Service.on_module_destroy = service_destroy

    # 2. Every rebuilt service was shut down once, consumers before dependencies.
    assert order == ["outer", "service", "fake"]
    assert (rebuilt_outer.destroyed, rebuilt_service.destroyed, fake.destroyed) == (1, 1, 1)
    # 3. The originals are restored by identity and untouched.
    assert container.resolve(Outer) is original_outer
    assert container.resolve(Service) is original_service
    assert container.resolve(Repo) is original_repo
    assert (original_outer.destroyed, original_service.destroyed) == (0, 0)
