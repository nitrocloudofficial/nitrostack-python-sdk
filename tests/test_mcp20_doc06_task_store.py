"""Tests for MCP 2.0 task store and distributed persistence (Doc 06)."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.core.task import TaskManager
from nitrostack.core.errors import TaskNotFoundError
from nitrostack.tasks.eviction import is_terminal_eviction_eligible, should_evict_terminal_task
from nitrostack.tasks.memory import InMemoryTaskStore
from nitrostack.tasks.store import TaskStore
from nitrostack.tasks.types import TaskEntry, TaskWireData, datetime_to_ms, utc_now


class TestTaskStoreContract:
    def test_in_memory_store_implements_interface(self):
        store = InMemoryTaskStore()
        assert isinstance(store, TaskStore)

    def test_store_round_trip(self):
        async def _run():
            store = InMemoryTaskStore()
            now = utc_now()
            wire = TaskWireData(task_id="t1", status="working", created_at=now, last_updated_at=now)
            entry = TaskEntry(task_id="t1", data=wire, status="working")
            await store.set("t1", entry)
            assert await store.has("t1") is True
            loaded = await store.get("t1")
            assert loaded is not None
            assert loaded.task_id == "t1"
            assert await store.delete("t1") is True
            assert await store.get("t1") is None

        asyncio.run(_run())


class TestTerminalEvictionRules:
    def test_active_tasks_not_eviction_eligible(self):
        assert is_terminal_eviction_eligible("working") is False
        assert is_terminal_eviction_eligible("input_required") is False
        assert is_terminal_eviction_eligible("completed") is True

    def test_terminal_task_evicted_after_ttl_from_last_updated(self):
        now = utc_now()
        wire = TaskWireData(
            task_id="t2",
            status="completed",
            created_at=now,
            last_updated_at=now,
            ttl_ms=1_000,
        )
        entry = TaskEntry(task_id="t2", data=wire, status="completed")
        now_ms = datetime_to_ms(now) + 2_000
        assert should_evict_terminal_task(entry, now_ms) is True

    def test_active_task_never_evicted_even_when_stale(self):
        now = utc_now()
        wire = TaskWireData(
            task_id="t3",
            status="working",
            created_at=now,
            last_updated_at=now,
            ttl_ms=1,
        )
        entry = TaskEntry(task_id="t3", data=wire, status="working")
        now_ms = datetime_to_ms(now) + 10_000
        assert should_evict_terminal_task(entry, now_ms) is False


class TestTaskManagerStoreIntegration:
    def test_manager_uses_injected_store(self):
        async def _run():
            store = InMemoryTaskStore()
            manager = TaskManager(store=store)
            task = await manager.create_task(ttl_ms=500)
            assert await store.has(task.id)
            await manager.complete_task(task.id, {"done": True})
            entries = await store.list()
            assert len(entries) == 1
            assert entries[0].result == {"done": True}

        asyncio.run(_run())

    def test_cleanup_expired_removes_terminal_tasks_from_store(self):
        async def _run():
            manager = TaskManager()
            task = await manager.create_task(ttl_ms=100)
            await manager.complete_task(task.id, {"ok": True})
            entry = await manager._require_entry(task.id)
            entry.data.last_updated_at = utc_now()
            await manager._store.set(task.id, entry)
            import datetime

            entry.data.last_updated_at = utc_now() - datetime.timedelta(milliseconds=200)
            await manager._store.set(task.id, entry)
            evicted = await manager.cleanup_expired()
            assert evicted == 1
            with pytest.raises(TaskNotFoundError):
                await manager.get_task(task.id)

        asyncio.run(_run())

    def test_shared_store_across_managers(self):
        """Simulates two replicas reading the same in-memory store."""
        async def _run():
            store = InMemoryTaskStore()
            replica_a = TaskManager(store=store)
            replica_b = TaskManager(store=store)
            task = await replica_a.create_task(ttl_ms=60_000)
            await replica_a.complete_task(task.id, {"from": "replica-a"})
            fetched = await replica_b.get_task(task.id)
            assert fetched.result == {"from": "replica-a"}

        asyncio.run(_run())
