"""SEP-2243 method contracts for the 2026 method surface.

Official v2 owns this table once mounted. Until then the sidecar validator
reads one row per method: required ``Mcp-Method``, optional ``Mcp-Name`` field,
and whether ``auto`` requires the method header.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nitrostack.protocol.version import ProtocolEra, WireMode

NAME_FIELD_NAME = "name"
NAME_FIELD_URI = "uri"


@dataclass(frozen=True)
class MethodContract:
    """Header contract for one JSON-RPC method."""

    method: str
    name_field: Optional[str] = None
    # ``auto`` keeps handshake / ping / discover optional (2025 clients).
    # ``modern`` requires ``Mcp-Method`` on every JSON-RPC POST.
    requires_method_header: bool = True


def _contract(
    method: str,
    *,
    name_field: Optional[str] = None,
    requires_method_header: bool = True,
) -> MethodContract:
    return MethodContract(
        method=method,
        name_field=name_field,
        requires_method_header=requires_method_header,
    )


MODERN_METHOD_CONTRACTS: tuple[MethodContract, ...] = (
    _contract("ping", requires_method_header=False),
    _contract("initialize", requires_method_header=False),
    _contract("notifications/initialized", requires_method_header=False),
    _contract("notifications/cancelled"),
    _contract("notifications/progress"),
    _contract("server/discover", requires_method_header=False),
    _contract("subscriptions/listen"),
    _contract("notifications/subscriptions/acknowledged"),
    _contract("tools/list"),
    _contract("tools/call", name_field=NAME_FIELD_NAME),
    _contract("notifications/tools/list_changed"),
    _contract("resources/list"),
    _contract("resources/templates/list"),
    _contract("resources/read", name_field=NAME_FIELD_URI),
    _contract("resources/subscribe", name_field=NAME_FIELD_URI),
    _contract("resources/unsubscribe", name_field=NAME_FIELD_URI),
    _contract("notifications/resources/list_changed"),
    _contract("notifications/resources/updated"),
    _contract("prompts/list"),
    _contract("prompts/get", name_field=NAME_FIELD_NAME),
    _contract("notifications/prompts/list_changed"),
    _contract("completion/complete"),
    _contract("tasks/get"),
    _contract("tasks/cancel"),
    _contract("tasks/result"),
    _contract("tasks/list"),
    _contract("logging/setLevel"),
)

_CONTRACTS_BY_METHOD: dict[str, MethodContract] = {
    row.method: row for row in MODERN_METHOD_CONTRACTS
}

NAME_SCOPED_METHODS: frozenset[str] = frozenset(
    row.method for row in MODERN_METHOD_CONTRACTS if row.name_field
)

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


def deprecated_method_message(method: str) -> Optional[str]:
    """Return the modern-wire rejection text when ``method`` is retired."""
    return DEPRECATED_MODERN_METHODS.get(method)


def rejects_deprecated_method(method: str, era: ProtocolEra) -> bool:
    """True when era ``modern`` must answer ``method`` as not found."""
    return era == "modern" and method in DEPRECATED_MODERN_METHODS


def contract_for(method: str) -> Optional[MethodContract]:
    """Return the table row for ``method``, or ``None`` when unlisted."""
    return _CONTRACTS_BY_METHOD.get(method)


def mcp_name_field(method: str) -> Optional[str]:
    """Body field mirrored by ``Mcp-Name``, if this method is name-scoped."""
    row = contract_for(method)
    return row.name_field if row is not None else None


def mcp_name_is_required(method: str) -> bool:
    """``Mcp-Name`` is required on ``tools/call``, ``resources/read``, ``prompts/get``."""
    return mcp_name_field(method) is not None and method in {
        "tools/call",
        "resources/read",
        "prompts/get",
    }


def mcp_method_is_required(method: str, wire_mode: WireMode) -> bool:
    """``modern`` requires ``Mcp-Method`` on every POST. ``auto`` follows the table."""
    if wire_mode == "reject":
        return True
    row = contract_for(method)
    if row is None:
        return False
    return row.requires_method_header
