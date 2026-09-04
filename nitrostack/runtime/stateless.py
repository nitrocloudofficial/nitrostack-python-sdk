"""Stateless HTTP invariants from Doc 00 §2 and Doc 01."""

from dataclasses import dataclass

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER


@dataclass(frozen=True)
class StatelessInvariants:
    """Architectural invariants for MCP 2026-07-28 stateless transport."""

    emit_session_headers: bool = False
    require_initialize_handshake: bool = False
    allow_session_stickiness: bool = False


DEFAULT_STATELESS_INVARIANTS = StatelessInvariants()


def assert_stateless_headers(response_headers: dict[str, str]) -> None:
    """Raise if a response violates stateless wire rules (no Mcp-Session-Id)."""
    for key in response_headers:
        if key.lower() == LEGACY_SESSION_HEADER.lower():
            raise ValueError(
                f"Stateless MCP MUST NOT emit '{LEGACY_SESSION_HEADER}' on responses"
            )
