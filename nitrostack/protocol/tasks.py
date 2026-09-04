"""MCP Tasks protocol helpers — Doc 05 (2026-07-28)."""

from __future__ import annotations

from typing import Any, Optional

RESULT_TYPE_TASK = "task"
DEFAULT_TASK_TTL_MS = 300_000
DEFAULT_POLL_INTERVAL_MS = 2_000


def ttl_ms_to_seconds(ttl_ms: Optional[int]) -> Optional[int]:
    """Convert wire TTL (milliseconds) to internal expiry seconds."""
    if ttl_ms is None:
        return None
    return max(1, int(ttl_ms / 1000))


def ttl_seconds_to_ms(ttl_seconds: Optional[int]) -> Optional[int]:
    """Convert internal TTL seconds to wire milliseconds."""
    if ttl_seconds is None:
        return None
    return int(ttl_seconds * 1000)


def build_task_create_jsonrpc_result(request_id: Any, task: dict[str, Any]) -> dict[str, Any]:
    """Build Doc 05 §4.2 task-augmented ``tools/call`` success envelope."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "resultType": RESULT_TYPE_TASK,
            "task": task,
        },
    }


def task_support_forbidden_message(tool_name: str) -> str:
    return f"Tool '{tool_name}' does not support task augmentation"


def task_support_required_message(tool_name: str) -> str:
    return f"Task augmentation required for tool '{tool_name}'"
