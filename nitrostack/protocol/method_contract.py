"""Methods retired from the 2026-07-28 wire.

SEP-2243 header enforcement belongs to the official MCP transport.
This module keeps only the shared retirement policy used by handlers.
"""

from __future__ import annotations

from nitrostack.protocol.version import ProtocolEra

# 2025 methods removed from the 2026-07-28 wire. Handshake methods
# (``initialize``, ``notifications/initialized``) are not in this table:
# ``modern`` rejects them as method-not-found; ``auto`` still answers them.
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


def deprecated_method_message(method: str) -> str | None:
    """Return the modern-wire rejection text when ``method`` is retired."""
    return DEPRECATED_MODERN_METHODS.get(method)


def rejects_deprecated_method(method: str, era: ProtocolEra) -> bool:
    """True when era ``modern`` must answer ``method`` as not found."""
    return era == "modern" and method in DEPRECATED_MODERN_METHODS
