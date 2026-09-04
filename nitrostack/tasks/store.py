"""Pluggable task persistence interface (Doc 00 principle 3; Doc 06 contract)."""

from abc import ABC, abstractmethod
from typing import List, Optional

from nitrostack.tasks.types import TaskEntry


class TaskStore(ABC):
    """Abstract storage adapter decoupling TaskManager from persistence backend."""

    @abstractmethod
    async def get(self, task_id: str) -> Optional[TaskEntry]:
        """Fetch task entry by ID."""

    @abstractmethod
    async def set(self, task_id: str, entry: TaskEntry) -> None:
        """Save or update task entry atomically."""

    @abstractmethod
    async def delete(self, task_id: str) -> bool:
        """Remove task entry. Returns True if it existed."""

    @abstractmethod
    async def has(self, task_id: str) -> bool:
        """Check existence without deserializing the full payload."""

    @abstractmethod
    async def list(self) -> List[TaskEntry]:
        """Return all stored entries (for pagination and TTL sweep)."""

    @abstractmethod
    async def cleanup_expired(self, now_ms: int) -> int:
        """Evict expired terminal tasks. Returns count evicted."""

    async def destroy(self) -> None:
        """Release connections, timers, and background resources."""
