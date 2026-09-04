"""
MCP Task state machine (Phase 1 / Doc 05–06).

``TaskManager`` owns lifecycle logic; persistence is delegated to a pluggable
``TaskStore`` (default: ``InMemoryTaskStore``).

Doc 06 eviction invariants:
- ``working`` / ``input_required`` tasks are never evicted.
- TTL countdown starts only after a terminal transition.
- ``cleanup_expired(now_ms)`` removes terminal tasks where
  ``(now_ms - lastUpdatedAt) > ttl_ms``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional

from nitrostack.core.errors import (
    InvalidTaskTransitionError,
    TaskAlreadyTerminalError,
    TaskExpiredError,
    TaskNotFoundError,
)
from nitrostack.protocol.tasks import DEFAULT_POLL_INTERVAL_MS, ttl_seconds_to_ms
from nitrostack.tasks.memory import InMemoryTaskStore
from nitrostack.tasks.store import TaskStore
from nitrostack.tasks.types import TaskEntry, TaskWireData, datetime_to_ms, utc_now


class TaskStatus(Enum):
    """Task lifecycle statuses required by Phase 1 / Doc 05."""

    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ACTIVE_STATUSES = frozenset({TaskStatus.WORKING, TaskStatus.INPUT_REQUIRED})

TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    }
)


def is_terminal_status(status: TaskStatus) -> bool:
    """Return True if ``status`` allows no further transitions."""
    return status in TERMINAL_STATUSES


def _status_from_wire(value: str) -> TaskStatus:
    return TaskStatus(value)


def _status_to_wire(status: TaskStatus) -> str:
    return status.value


@dataclass
class TaskData:
    """
    Snapshot of a task's protocol-visible and result state.

    ``progress`` holds the latest progress/status message. ``result`` / ``error``
    are populated on successful completion or failure respectively.
    """

    id: str
    status: TaskStatus
    progress: Optional[str] = None
    result: Any = None
    error: Any = None
    created_at: Any = field(default_factory=utc_now)
    expires_at: Optional[Any] = None
    last_updated_at: Optional[Any] = None
    ttl_seconds: Optional[int] = None
    ttl_ms: Optional[int] = None
    poll_interval: int = DEFAULT_POLL_INTERVAL_MS

    def __post_init__(self) -> None:
        if self.last_updated_at is None:
            self.last_updated_at = self.created_at

    @property
    def task_id(self) -> str:
        return self.id

    @property
    def status_message(self) -> Optional[str]:
        return self.progress

    @property
    def ttl(self) -> Optional[int]:
        return self.ttl_ms if self.ttl_ms is not None else ttl_seconds_to_ms(self.ttl_seconds)


@dataclass
class _RuntimeTaskHandle:
    """Local-only execution primitives (not persisted to distributed stores)."""

    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False


class TaskManager:
    """
    Task lifecycle manager backed by a pluggable ``TaskStore``.

    Runtime wait/cancel handles remain process-local even when using Redis or
    PostgreSQL persistence.
    """

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self._store = store or InMemoryTaskStore()
        self._runtime: Dict[str, _RuntimeTaskHandle] = {}

    async def create_task(
        self,
        ttl_seconds: Optional[int] = None,
        *,
        ttl_ms: Optional[int] = None,
        task_id: Optional[str] = None,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
        tool_name: Optional[str] = None,
        owner_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> TaskData:
        """Create a new task in ``WORKING`` status."""
        now = utc_now()
        resolved_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        if await self._store.has(resolved_id):
            raise ValueError(f"Task {resolved_id} already exists")

        resolved_ttl_seconds = ttl_seconds
        resolved_ttl_ms = ttl_ms
        if resolved_ttl_ms is not None and resolved_ttl_seconds is None:
            resolved_ttl_seconds = max(1, int(resolved_ttl_ms / 1000))
        elif resolved_ttl_seconds is not None and resolved_ttl_ms is None:
            resolved_ttl_ms = ttl_seconds_to_ms(resolved_ttl_seconds)

        wire = TaskWireData(
            task_id=resolved_id,
            status="working",
            status_message="Task created",
            created_at=now,
            last_updated_at=now,
            ttl_ms=resolved_ttl_ms,
            poll_interval_ms=poll_interval_ms,
            owner_id=owner_id,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        entry = TaskEntry(
            task_id=resolved_id,
            data=wire,
            status="working",
            tool_name=tool_name,
            owner_id=owner_id,
            tenant_id=tenant_id,
            session_id=session_id,
        )
        await self._store.set(resolved_id, entry)
        self._runtime[resolved_id] = _RuntimeTaskHandle()
        return self._snapshot_from_entry(entry)

    async def get_task(self, task_id: str) -> TaskData:
        """Return a snapshot of the task or raise ``TaskNotFoundError``."""
        entry = await self._require_entry(task_id)
        return self._snapshot_from_entry(entry)

    async def update_progress(self, task_id: str, progress: Any) -> None:
        """Update progress for an active task."""
        entry = await self._require_entry(task_id)
        status = _status_from_wire(entry.status)
        if is_terminal_status(status):
            if status == TaskStatus.EXPIRED:
                raise TaskExpiredError(task_id)
            raise TaskAlreadyTerminalError(task_id, status)
        now = utc_now()
        entry.data.status_message = str(progress)
        entry.data.last_updated_at = now
        entry.status = entry.data.status
        await self._store.set(task_id, entry)

    async def require_input(
        self,
        task_id: str,
        pause_payload: Any,
        *,
        progress: str = "Additional input required",
    ) -> None:
        """Transition an active task to ``input_required``."""
        entry = await self._require_entry(task_id)
        status = _status_from_wire(entry.status)
        if is_terminal_status(status):
            if status == TaskStatus.EXPIRED:
                raise TaskExpiredError(task_id)
            raise TaskAlreadyTerminalError(task_id, status)
        if status not in ACTIVE_STATUSES:
            raise InvalidTaskTransitionError(status, TaskStatus.INPUT_REQUIRED)
        now = utc_now()
        entry.result = pause_payload
        entry.status = "input_required"
        entry.data.status = "input_required"
        entry.data.status_message = progress
        entry.data.last_updated_at = now
        await self._store.set(task_id, entry)

    async def resume_task(self, task_id: str, *, progress: str = "Resuming task") -> None:
        """Transition ``input_required`` back to ``working``."""
        entry = await self._require_entry(task_id)
        status = _status_from_wire(entry.status)
        if status != TaskStatus.INPUT_REQUIRED:
            raise InvalidTaskTransitionError(status, TaskStatus.WORKING)
        now = utc_now()
        entry.status = "working"
        entry.data.status = "working"
        entry.data.status_message = progress
        entry.data.last_updated_at = now
        await self._store.set(task_id, entry)

    async def complete_task(self, task_id: str, result: Any) -> None:
        """Transition an active task to ``completed``."""
        entry = await self._require_entry(task_id)
        self._require_active_for_transition(entry, TaskStatus.COMPLETED)
        entry.result = result
        entry.error = None
        entry.status = "completed"
        entry.data.status = "completed"
        entry.data.status_message = "Task completed successfully"
        entry.data.last_updated_at = utc_now()
        await self._store.set(task_id, entry)
        self._signal_done(task_id)

    async def fail_task(self, task_id: str, error: Any) -> None:
        """Transition an active task to ``failed``."""
        entry = await self._require_entry(task_id)
        self._require_active_for_transition(entry, TaskStatus.FAILED)
        entry.error = {"message": str(error)}
        entry.status = "failed"
        entry.data.status = "failed"
        entry.data.status_message = f"Task failed: {error}"
        entry.data.last_updated_at = utc_now()
        await self._store.set(task_id, entry)
        self._signal_done(task_id)

    async def cancel_task(self, task_id: str) -> None:
        """Transition an active task to ``cancelled``."""
        entry = await self._require_entry(task_id)
        status = _status_from_wire(entry.status)
        if is_terminal_status(status):
            if status == TaskStatus.EXPIRED:
                raise TaskExpiredError(task_id)
            raise TaskAlreadyTerminalError(task_id, status)
        entry.status = "cancelled"
        entry.data.status = "cancelled"
        entry.data.status_message = "Task cancelled by client"
        entry.data.last_updated_at = utc_now()
        await self._store.set(task_id, entry)
        handle = self._runtime.setdefault(task_id, _RuntimeTaskHandle())
        handle.cancelled = True
        self._signal_done(task_id)

    async def list_tasks(self) -> List[TaskData]:
        """Return snapshots for all known tasks."""
        entries = await self._store.list()
        return [self._snapshot_from_entry(entry) for entry in entries]

    async def has_task(self, task_id: str) -> bool:
        return await self._store.has(task_id)

    async def is_task_cancelled(self, task_id: str) -> bool:
        handle = self._runtime.get(task_id)
        if handle is not None and handle.cancelled:
            return True
        if not await self._store.has(task_id):
            return False
        entry = await self._store.get(task_id)
        return entry is not None and entry.status == "cancelled"

    def is_task_cancelled_sync(self, task_id: str) -> bool:
        """Best-effort synchronous cancel probe for ``TaskContext.throw_if_cancelled``."""
        handle = self._runtime.get(task_id)
        if handle is not None and handle.cancelled:
            return True
        return False

    async def wait_until_done(self, task_id: str) -> TaskData:
        """Block until the task reaches a terminal state."""
        entry = await self._require_entry(task_id)
        status = _status_from_wire(entry.status)
        if not is_terminal_status(status):
            handle = self._runtime.setdefault(task_id, _RuntimeTaskHandle())
            await handle.done_event.wait()
            entry = await self._require_entry(task_id)
        return self._snapshot_from_entry(entry)

    async def get_result(self, task_id: str) -> Any:
        """Return the stored result for a completed task."""
        data = await self.get_task(task_id)
        if data.status == TaskStatus.EXPIRED:
            raise TaskExpiredError(task_id)
        if data.status != TaskStatus.COMPLETED:
            raise InvalidTaskTransitionError(data.status, TaskStatus.COMPLETED)
        return data.result

    async def cleanup_expired(self, now_ms: Optional[int] = None) -> int:
        """Evict terminal tasks whose post-completion TTL has elapsed."""
        resolved_now = now_ms if now_ms is not None else datetime_to_ms(utc_now())
        evicted = await self._store.cleanup_expired(resolved_now)
        for task_id in list(self._runtime.keys()):
            if not await self._store.has(task_id):
                self._runtime.pop(task_id, None)
        return evicted

    async def destroy(self) -> None:
        await self._store.destroy()
        self._runtime.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _require_entry(self, task_id: str) -> TaskEntry:
        entry = await self._store.get(task_id)
        if entry is None:
            raise TaskNotFoundError(task_id)
        return entry

    def _require_active_for_transition(self, entry: TaskEntry, to_status: TaskStatus) -> None:
        current = _status_from_wire(entry.status)
        if current == TaskStatus.EXPIRED:
            raise TaskExpiredError(entry.task_id)
        if is_terminal_status(current):
            raise TaskAlreadyTerminalError(entry.task_id, current)
        if current not in ACTIVE_STATUSES:
            raise InvalidTaskTransitionError(current, to_status)

    def _signal_done(self, task_id: str) -> None:
        handle = self._runtime.setdefault(task_id, _RuntimeTaskHandle())
        handle.done_event.set()

    @staticmethod
    def _snapshot_from_entry(entry: TaskEntry) -> TaskData:
        ttl_ms = entry.data.ttl_ms
        ttl_seconds = max(1, int(ttl_ms / 1000)) if ttl_ms is not None else None
        return TaskData(
            id=entry.task_id,
            status=_status_from_wire(entry.status),
            progress=entry.data.status_message,
            result=entry.result,
            error=entry.error,
            created_at=entry.data.created_at,
            last_updated_at=entry.data.last_updated_at,
            expires_at=None,
            ttl_seconds=ttl_seconds,
            ttl_ms=ttl_ms,
            poll_interval=entry.data.poll_interval_ms,
        )

    @staticmethod
    def _snapshot(data: TaskData) -> TaskData:
        return replace(data)
