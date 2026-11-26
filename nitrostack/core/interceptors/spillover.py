"""Bounded, caller-scoped storage for serialized tool results."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nitrostack.core.errors import PayloadTooLargeError


@dataclass(frozen=True)
class SpilloverEntry:
    payload: bytes
    owner: str
    mime_type: str
    expires_at: float


class SpilloverStore(Protocol):
    max_bytes: int
    ttl_seconds: float

    async def put(
        self, payload: bytes, *, owner: str, mime_type: str,
        ttl_seconds: float | None = None,
    ) -> str: ...

    async def get(self, key: str, *, owner: str) -> SpilloverEntry | None: ...

    async def delete(self, key: str, *, owner: str) -> bool: ...

    async def cleanup_expired(self) -> int: ...


def _valid_key(key: str) -> bool:
    return isinstance(key, str) and re.fullmatch(r"[0-9a-f]{32}", key) is not None


class MemorySpilloverStore:
    """Thread-safe payload cache with absolute TTL and LRU eviction."""

    def __init__(
        self, *, max_bytes: int = 10 * 1024 * 1024,
        max_total_bytes: int = 100 * 1024 * 1024, ttl_seconds: float = 300,
    ):
        if any(type(limit) is not int or limit <= 0 for limit in (max_bytes, max_total_bytes)):
            raise ValueError("Storage limits must be positive integers")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("TTL must be positive and finite")
        self.max_bytes = max_bytes
        self.max_total_bytes = max_total_bytes
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, SpilloverEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._size = 0

    def _entry(self, payload, owner, mime_type, ttl_seconds):
        if not isinstance(payload, bytes):
            raise TypeError("Store payload must be bytes")
        if not owner:
            raise ValueError("A caller owner is required")
        if len(payload) > self.max_bytes:
            raise PayloadTooLargeError(f"Payload exceeds max_bytes={self.max_bytes}")
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("TTL must be positive and finite")
        return SpilloverEntry(payload, owner, mime_type, time.time() + ttl)

    def _remove(self, key):
        entry = self._entries.pop(key)
        self._size -= len(entry.payload)

    def _cleanup(self):
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= time.time()]
        for key in expired:
            self._remove(key)
        return len(expired)

    async def put(self, payload, *, owner, mime_type, ttl_seconds=None):
        entry = self._entry(payload, owner, mime_type, ttl_seconds)
        if len(payload) > self.max_total_bytes:
            raise ValueError("Payload exceeds total store capacity")
        with self._lock:
            self._cleanup()
            while self._size + len(payload) > self.max_total_bytes:
                self._remove(next(iter(self._entries)))
            key = uuid.uuid4().hex
            self._entries[key] = entry
            self._size += len(payload)
            return key

    async def get(self, key, *, owner):
        with self._lock:
            self._cleanup()
            entry = self._entries.get(key) if _valid_key(key) else None
            if entry is None or entry.owner != owner:
                return None
            self._entries.move_to_end(key)
            return entry

    async def delete(self, key, *, owner):
        with self._lock:
            entry = self._entries.get(key) if _valid_key(key) else None
            if entry is None or entry.owner != owner:
                return False
            self._remove(key)
            return True

    async def cleanup_expired(self):
        with self._lock:
            return self._cleanup()


class FileSpilloverStore(MemorySpilloverStore):
    """Atomic disk store. Each root must be owned by one store instance/process.

    Total capacity counts actual JSON file bytes, including base64 and metadata.
    File operations run in worker threads; no background cleanup task is needed.
    """

    def __init__(self, root: str | Path, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._cleanup_files()

    def _path(self, key):
        if not _valid_key(key):
            raise ValueError("Invalid spillover ID")
        path = self.root / f"{key}.json"
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Spillover path escapes storage root")
        return path

    def _read(self, path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SpilloverEntry(
            base64.b64decode(raw["payload"], validate=True), raw["owner"],
            raw["mime_type"], raw["expires_at"],
        )

    def _cleanup_files(self):
        count = 0
        for candidate in self.root.glob("*.json"):
            if not _valid_key(candidate.stem):
                continue
            path = self._path(candidate.stem)
            if self._read(path).expires_at <= time.time():
                path.unlink()
                count += 1
        return count

    def _put_file(self, payload, owner, mime_type, ttl_seconds):
        entry = self._entry(payload, owner, mime_type, ttl_seconds)
        encoded = json.dumps({
            "payload": base64.b64encode(payload).decode("ascii"),
            "owner": entry.owner, "mime_type": entry.mime_type,
            "expires_at": entry.expires_at,
        }).encode("utf-8")
        with self._lock:
            self._cleanup_files()
            used = sum(self._path(p.stem).stat().st_size for p in self.root.glob("*.json")
                       if _valid_key(p.stem))
            if used + len(encoded) > self.max_total_bytes:
                raise ValueError("File store capacity exceeded")
            key = uuid.uuid4().hex
            target = self._path(key)
            temporary = self.root / f"{key}.tmp"
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return key

    def _get_file(self, key, owner, delete=False):
        with self._lock:
            path = self._path(key)
            self._cleanup_files()
            if not path.exists():
                return None
            entry = self._read(path)
            if entry.expires_at <= time.time():
                path.unlink()
                return None
            if entry.owner != owner:
                return None
            if delete:
                path.unlink()
            return entry

    async def put(self, payload, *, owner, mime_type, ttl_seconds=None):
        return await asyncio.to_thread(self._put_file, payload, owner, mime_type, ttl_seconds)

    async def get(self, key, *, owner):
        return await asyncio.to_thread(self._get_file, key, owner)

    async def delete(self, key, *, owner):
        return await asyncio.to_thread(self._get_file, key, owner, True) is not None

    async def cleanup_expired(self):
        def cleanup():
            with self._lock:
                return self._cleanup_files()
        return await asyncio.to_thread(cleanup)
