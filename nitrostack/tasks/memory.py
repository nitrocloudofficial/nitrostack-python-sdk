"""In-memory TaskStore for development and single-replica deployments."""

from __future__ import annotations

from typing import Dict, List, Optional

from nitrostack.tasks.eviction import should_evict_terminal_task
from nitrostack.tasks.store import TaskStore
from nitrostack.tasks.types import TaskEntry


class InMemoryTaskStore(TaskStore):
    """Process-local task persistence with terminal-only TTL eviction."""

    def __init__(self) -> None:
        self._entries: Dict[str, TaskEntry] = {}

    async def get(self, task_id: str) -> Optional[TaskEntry]:
        return self._entries.get(task_id)

    async def set(self, task_id: str, entry: TaskEntry) -> None:
        self._entries[task_id] = entry

    async def delete(self, task_id: str) -> bool:
        return self._entries.pop(task_id, None) is not None

    async def has(self, task_id: str) -> bool:
        return task_id in self._entries

    async def list(self) -> List[TaskEntry]:
        return list(self._entries.values())

    async def cleanup_expired(self, now_ms: int) -> int:
        evicted = 0
        for task_id in list(self._entries.keys()):
            entry = self._entries[task_id]
            if should_evict_terminal_task(entry, now_ms):
                del self._entries[task_id]
                evicted += 1
        return evicted

    async def destroy(self) -> None:
        self._entries.clear()
