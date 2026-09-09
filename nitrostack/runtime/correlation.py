"""Per-request correlation for overlapping JSON-RPC calls.

Client JSON-RPC ``id`` values are echoed on the wire only. In-flight work,
progress, and cancel target an internal correlation id so two concurrent
POSTs that reuse ``id: 1`` stay isolated.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


def new_correlation_id() -> str:
    return str(uuid.uuid4())


@dataclass
class InFlightTicket:
    """One in-flight handler, keyed only by ``correlation_id``."""

    correlation_id: str
    jsonrpc_id: Any = None
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)

    def request_cancel(self) -> None:
        self.cancel_requested.set()


class InFlightRegistry:
    """In-flight map that never indexes by the client JSON-RPC ``id``."""

    def __init__(self) -> None:
        self._tickets: dict[str, InFlightTicket] = {}

    def register(self, correlation_id: str, *, jsonrpc_id: Any = None) -> InFlightTicket:
        ticket = InFlightTicket(correlation_id=correlation_id, jsonrpc_id=jsonrpc_id)
        self._tickets[correlation_id] = ticket
        return ticket

    def get(self, correlation_id: str) -> Optional[InFlightTicket]:
        return self._tickets.get(correlation_id)

    def discard(self, correlation_id: str) -> None:
        self._tickets.pop(correlation_id, None)

    def cancel(self, correlation_id: str) -> bool:
        """Cancel one ticket. Same client ``id`` on another ticket is untouched."""
        ticket = self._tickets.get(correlation_id)
        if ticket is None:
            return False
        ticket.request_cancel()
        return True

    def __len__(self) -> int:
        return len(self._tickets)

    def __iter__(self) -> Iterator[InFlightTicket]:
        return iter(list(self._tickets.values()))
