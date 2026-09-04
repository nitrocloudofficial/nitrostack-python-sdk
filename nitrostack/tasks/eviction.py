"""Terminal-only TTL eviction rules (Doc 06 §4)."""

from __future__ import annotations

from nitrostack.tasks.types import TERMINAL_TASK_STATUSES, TaskEntry


def is_terminal_eviction_eligible(status: str) -> bool:
    """Only completed, failed, and cancelled tasks may be evicted."""
    return status in TERMINAL_TASK_STATUSES


def should_evict_terminal_task(entry: TaskEntry, now_ms: int) -> bool:
    """
    Return True when a terminal task exceeded its post-completion TTL.

    Active tasks (``working``, ``input_required``) are never evicted.
    """
    if not is_terminal_eviction_eligible(entry.status):
        return False
    ttl_ms = entry.data.ttl_ms
    if ttl_ms is None:
        return False
    last_updated_ms = entry.data.last_updated_at_ms
    return (now_ms - last_updated_ms) > ttl_ms
