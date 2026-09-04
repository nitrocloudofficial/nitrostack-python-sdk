"""Deprecated MCP methods rejected on the 2026-07-28 wire (Doc 02 §4)."""

from __future__ import annotations

from typing import Optional

DEPRECATED_MODERN_METHODS: dict[str, str] = {
    "tasks/result": "Method 'tasks/result' is not supported in MCP 2026-07-28; use 'tasks/get'.",
    "tasks/list": "Method 'tasks/list' is not supported in modern stateless MCP 2026-07-28.",
    "resources/subscribe": (
        "Method 'resources/subscribe' is not supported in stateless MCP 2026-07-28; "
        "use SSE subscriptions/listen."
    ),
    "logging/setLevel": (
        "Method 'logging/setLevel' is not supported in stateless MCP 2026-07-28; "
        "configure logging at the host level."
    ),
}


def deprecated_method_message(method: str) -> Optional[str]:
    """Return rejection message if method is deprecated on modern wire."""
    return DEPRECATED_MODERN_METHODS.get(method)
