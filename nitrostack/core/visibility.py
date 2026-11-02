"""Per-session tool visibility with bounded TTL/LRU state."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable, Iterable


@dataclass
class _SessionVisibility:
    expires_at: float
    overrides: dict[str, bool] = field(default_factory=dict)


class SessionVisibilityStore:
    """Thread-safe TTL/LRU storage for tool visibility overrides."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], _SessionVisibility] = OrderedDict()
        self._lock = RLock()

    def _expire(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                del self._entries[key]

    def _get(self, key: tuple[str, str], now: float) -> _SessionVisibility:
        self._expire(now)
        entry = self._entries.get(key)
        if entry is None:
            entry = _SessionVisibility(now + self.ttl_seconds)
            self._entries[key] = entry
        else:
            self._entries.move_to_end(key)
            entry.expires_at = now + self.ttl_seconds
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return entry

    def is_visible(self, key: tuple[str, str], name: str, default: bool) -> bool:
        with self._lock:
            return self._get(key, self._clock()).overrides.get(name, default)

    def update(self, key: tuple[str, str], names: Iterable[str], visible: bool) -> bool:
        changed = False
        with self._lock:
            entry = self._get(key, self._clock())
            for name in names:
                if entry.overrides.get(name) != visible:
                    entry.overrides[name] = visible
                    changed = True
        return changed


class VisibilityTransform:
    """Apply decorator defaults and per-session tool visibility overrides."""

    def __init__(self, store: SessionVisibilityStore | None = None) -> None:
        self.store = store or SessionVisibilityStore()

    @staticmethod
    def session_key(
        transport_session_id: str | None,
        verified_subject: str | None,
    ) -> tuple[str, str] | None:
        if not transport_session_id:
            return None
        return transport_session_id, verified_subject or ""

    def is_visible(self, entry: object, key: tuple[str, str] | None) -> bool:
        config = getattr(entry, "config")
        default = config.default_visible
        if default is None:
            default = config.visibility == "visible"
        if key is None:
            return default
        return self.store.is_visible(key, config.name, default)

    def update(self, key: tuple[str, str], names: Iterable[str], visible: bool) -> bool:
        """Update known tool names for a specific visibility key."""
        return self.store.update(key, names, visible)

    def change(self, context: object, names: Iterable[str], *, visible: bool) -> bool:
        key = getattr(context, "_visibility_session_key", None)
        if key is None:
            getattr(context, "logger").warn(
                "Tool visibility changes require a sessionful transport session"
            )
            return False
        app = getattr(context, "_visibility_app")
        valid: list[str] = []
        for name in names:
            if name not in app._tools:
                getattr(context, "logger").warn(f"Unknown tool name in visibility change: {name}")
                continue
            valid.append(name)
        return self.store.update(key, valid, visible) if valid else False
