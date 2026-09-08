"""Stateless runtime policies and invariants for MCP 2026-07-28."""

from nitrostack.runtime.correlation import InFlightRegistry, InFlightTicket, new_correlation_id
from nitrostack.runtime.stateless import (
    StatelessInvariants,
    assert_stateless_headers,
    has_incoming_session_id,
    is_unsupported_protocol_version,
    request_protocol_version,
    sessionless_rejects_incoming_session_id,
)

__all__ = [
    "InFlightRegistry",
    "InFlightTicket",
    "new_correlation_id",
    "StatelessInvariants",
    "assert_stateless_headers",
    "has_incoming_session_id",
    "is_unsupported_protocol_version",
    "request_protocol_version",
    "sessionless_rejects_incoming_session_id",
]
