"""Async token persistence adapted from the unmerged full-parity implementation.

Expiry is milliseconds since the Unix epoch; durations and skew are seconds.
Expired records remain readable so their refresh credentials are not discarded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


class TokenStoreError(ValueError):
    """Persistence or record validation failed; messages never contain credentials."""


@dataclass(frozen=True)
class StoredToken:
    access_token: str = field(repr=False)
    token_type: str = field(repr=False)
    expires_at: int
    refresh_token: str | None = field(default=None, repr=False)
    scope: str | None = field(default=None, repr=False)
    resource: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.access_token, str) or not self.access_token:
            raise TokenStoreError("Invalid access token")
        if not isinstance(self.token_type, str) or not self.token_type:
            raise TokenStoreError("Invalid token type")
        if type(self.expires_at) is not int or self.expires_at < 0:
            raise TokenStoreError("Invalid token expiry")
        if any(value is not None and not isinstance(value, str)
               for value in (self.refresh_token, self.scope, self.resource)):
            raise TokenStoreError("Invalid optional token fields")

    def to_dict(self) -> dict[str, Any]:
        """Explicit credential-bearing serialization. Never log this mapping."""
        return {"access_token": self.access_token, "token_type": self.token_type,
                "expires_at": self.expires_at, "refresh_token": self.refresh_token,
                "scope": self.scope, "resource": self.resource}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StoredToken:
        try:
            return cls(**dict(value))
        except (TypeError, ValueError):
            raise TokenStoreError("Invalid stored token record") from None


def _seconds(value: float, label: str) -> float:
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be finite, nonnegative seconds")
    return value


def calculate_expiration(expires_in: float) -> int:
    deadline = time.time() * 1000 + _seconds(expires_in, "expires_in") * 1000
    if not math.isfinite(deadline):
        raise ValueError("expires_in is too large")
    return int(deadline)


def is_token_expired(token: StoredToken, skew: float = 30) -> bool:
    return time.time() * 1000 >= token.expires_at - _seconds(skew, "skew") * 1000


def token_response_to_stored(
    response: Mapping[str, Any], resource: str | None = None,
    *, previous: StoredToken | None = None,
) -> StoredToken:
    """Use a one-hour fallback only for absent expiry, preserving refresh rotation."""
    try:
        expires_in = response.get("expires_in")
        return StoredToken(
            access_token=response["access_token"],
            token_type=response.get("token_type", "Bearer"),
            expires_at=calculate_expiration(3600 if expires_in is None else expires_in),
            refresh_token=response.get("refresh_token", previous.refresh_token if previous else None),
            scope=response.get("scope", previous.scope if previous else None),
            resource=previous.resource if previous else resource,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise TokenStoreError("Invalid OAuth token response") from None


class TokenStore(Protocol):
    async def save_token(self, key: str, token: StoredToken) -> None: ...
    async def get_token(self, key: str) -> StoredToken | None: ...
    async def delete_token(self, key: str) -> None: ...
    async def list_keys(self) -> list[str]: ...
    async def clear(self) -> None: ...


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not key:
        raise TokenStoreError("A nonempty token key is required")


class MemoryTokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, StoredToken] = {}
        self._lock = threading.RLock()

    async def save_token(self, key: str, token: StoredToken) -> None:
        _validate_key(key)
        if not isinstance(token, StoredToken):
            raise TokenStoreError("Expected a StoredToken")
        with self._lock:
            self._tokens[key] = token

    async def get_token(self, key: str) -> StoredToken | None:
        _validate_key(key)
        with self._lock:
            return self._tokens.get(key)

    async def delete_token(self, key: str) -> None:
        _validate_key(key)
        with self._lock:
            self._tokens.pop(key, None)

    async def list_keys(self) -> list[str]:
        with self._lock:
            return list(self._tokens)

    async def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


_path_locks: dict[str, Any] = {}
_path_locks_guard = threading.Lock()


class FileTokenStore:
    """Atomic single-process storage, serialized across instances of the same path.

    POSIX files are mode 0600. Windows deployments must use a private parent ACL.
    Encryption retains the prior AES-GCM ``iv:tag:ciphertext`` hex file format.
    """

    def __init__(self, store_path: str | Path, encryption_key: str | None = None):
        self._path = Path(store_path).expanduser().absolute()
        if encryption_key is not None and (not isinstance(encryption_key, str) or not encryption_key):
            raise TokenStoreError("Encryption key must be a nonempty string")
        self._encryption_key = encryption_key
        canonical = os.path.normcase(str(self._path.parent.resolve() / self._path.name))
        with _path_locks_guard:
            self._lock = _path_locks.setdefault(canonical, threading.RLock())

    def _cipher_parts(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            raise TokenStoreError("Install nitrostack[token-encryption] for encrypted storage") from None
        key = hashlib.pbkdf2_hmac(
            "sha256", self._encryption_key.encode("utf-8"), b"nitrostack-token-store", 100000, 32,
        )
        return Cipher, algorithms, modes, key

    def _encrypt(self, text: str) -> str:
        if self._encryption_key is None:
            return text
        Cipher, algorithms, modes, key = self._cipher_parts()
        iv = os.urandom(16)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
        ciphertext = encryptor.update(text.encode("utf-8")) + encryptor.finalize()
        return f"{iv.hex()}:{encryptor.tag.hex()}:{ciphertext.hex()}"

    def _decrypt(self, text: str) -> str:
        if self._encryption_key is None:
            return text
        Cipher, algorithms, modes, key = self._cipher_parts()
        iv, tag, ciphertext = (bytes.fromhex(part) for part in text.split(":"))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
        return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")

    def _check_path(self) -> None:
        if self._path.is_symlink():
            raise TokenStoreError("Token store cannot be a symlink")

    def _load(self) -> dict[str, StoredToken]:
        self._check_path()
        try:
            descriptor = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {}
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise TokenStoreError("Token store must be a regular file")
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            text = stream.read()
        try:
            plaintext = self._decrypt(text)
        except TokenStoreError:
            raise
        except Exception:
            raise TokenStoreError("Token store decryption failed") from None
        try:
            raw = json.loads(plaintext)
        except (ValueError, TypeError):
            raise TokenStoreError("Token store contains invalid JSON") from None
        if not isinstance(raw, dict):
            raise TokenStoreError("Invalid token store document")
        for key in raw:
            _validate_key(key)
        return {key: StoredToken.from_dict(value) for key, value in raw.items()}

    def _save(self, tokens: dict[str, StoredToken]) -> None:
        self._check_path()
        data = self._encrypt(json.dumps({key: value.to_dict() for key, value in tokens.items()}))
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".oauth-token-", dir=self._path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                if os.name == "posix":
                    os.fchmod(stream.fileno(), 0o600)
                else:
                    os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_path()
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _operate(self, operation: str, key=None, token=None):
        try:
            with self._lock:
                tokens = self._load()
                if operation == "get":
                    return tokens.get(key)
                if operation == "list":
                    return list(tokens)
                if operation == "save":
                    tokens[key] = token
                elif operation == "delete":
                    tokens.pop(key, None)
                elif operation == "clear":
                    tokens.clear()
                self._save(tokens)
        except TokenStoreError:
            raise
        except PermissionError:
            raise TokenStoreError("Token store permission denied") from None
        except Exception:
            raise TokenStoreError("Token store operation failed") from None

    async def save_token(self, key: str, token: StoredToken) -> None:
        _validate_key(key)
        if not isinstance(token, StoredToken):
            raise TokenStoreError("Expected a StoredToken")
        await asyncio.to_thread(self._operate, "save", key, token)

    async def get_token(self, key: str) -> StoredToken | None:
        _validate_key(key)
        return await asyncio.to_thread(self._operate, "get", key)

    async def delete_token(self, key: str) -> None:
        _validate_key(key)
        await asyncio.to_thread(self._operate, "delete", key)

    async def list_keys(self) -> list[str]:
        return await asyncio.to_thread(self._operate, "list")

    async def clear(self) -> None:
        await asyncio.to_thread(self._operate, "clear")


def get_default_store_path() -> str:
    return str(Path.home() / ".nitrostack" / "tokens.json")


def create_default_token_store(
    store_path: str | Path | None = None, encryption_key: str | None = None,
) -> FileTokenStore:
    return FileTokenStore(store_path or get_default_store_path(), encryption_key)
