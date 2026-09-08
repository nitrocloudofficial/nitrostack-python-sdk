"""Stateless HTTP invariants for MCP 2026-07-28."""

from dataclasses import dataclass
from typing import Mapping

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER


@dataclass(frozen=True)
class StatelessInvariants:
    """Architectural invariants for MCP 2026-07-28 stateless transport."""

    emit_session_headers: bool = False
    require_initialize_handshake: bool = False
    allow_session_stickiness: bool = False


DEFAULT_STATELESS_INVARIANTS = StatelessInvariants()


def has_incoming_session_id(request_headers: Mapping[str, str]) -> bool:
    """True when the client sent a non-empty ``Mcp-Session-Id``."""
    target = LEGACY_SESSION_HEADER.lower()
    for key, value in request_headers.items():
        if key.lower() == target and str(value).strip():
            return True
    return False


def sessionless_rejects_incoming_session_id(wire_mode: str) -> bool:
    """``modern`` and ``auto`` engines have no session semantics. ``legacy`` does."""
    return wire_mode != "sessionful"


def assert_stateless_headers(response_headers: dict[str, str]) -> None:
    """Raise if a response violates stateless wire rules (no Mcp-Session-Id)."""
    for key in response_headers:
        if key.lower() == LEGACY_SESSION_HEADER.lower():
            raise ValueError(
                f"Stateless MCP MUST NOT emit '{LEGACY_SESSION_HEADER}' on responses"
            )
