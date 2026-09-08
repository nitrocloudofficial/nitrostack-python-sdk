"""Stateless HTTP invariants for MCP 2026-07-28."""

from dataclasses import dataclass
from typing import Mapping, Optional

from nitrostack.protocol.constants import LEGACY_SESSION_HEADER
from nitrostack.protocol.version import ProtocolEra, supported_protocol_versions_for_era


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


def request_protocol_version(
    header_version: Optional[str],
    envelope_version: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve the request protocol version.

    The header wins when present. Otherwise ``_meta.mcp.protocolVersion`` is
    used. When neither is present the request proceeds (legacy default). The
    header is not required on ``modern`` in this sidecar.
    """
    for raw in (header_version, envelope_version):
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def is_unsupported_protocol_version(
    version: Optional[str],
    era: ProtocolEra,
) -> bool:
    """True when a version is present and not in the era's supported set."""
    if version is None or not str(version).strip():
        return False
    return version.strip() not in supported_protocol_versions_for_era(era)


def assert_stateless_headers(response_headers: dict[str, str]) -> None:
    """Raise if a response violates stateless wire rules (no Mcp-Session-Id)."""
    for key in response_headers:
        if key.lower() == LEGACY_SESSION_HEADER.lower():
            raise ValueError(
                f"Stateless MCP MUST NOT emit '{LEGACY_SESSION_HEADER}' on responses"
            )
