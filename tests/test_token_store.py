"""Issue #68 storage, expiry, encryption compatibility, and secret safety."""

import asyncio
import builtins
import hashlib
import json
import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from nitrostack import (
    FileTokenStore, MemoryTokenStore, StoredToken, TokenStoreError,
    calculate_expiration, create_default_token_store, get_default_store_path,
    is_token_expired, token_response_to_stored,
)

ACCESS = "ACCESS_SENTINEL_NOT_FOR_LOGS"
REFRESH = "REFRESH_SENTINEL_NOT_FOR_LOGS"
KEY = "ENCRYPTION_SENTINEL_NOT_FOR_LOGS"


def token(**overrides):
    return StoredToken(**{"access_token": ACCESS, "token_type": "Bearer", "expires_at": 123,
                          "refresh_token": REFRESH, "scope": "read", "resource": "https://resource.example",
                          **overrides})


@pytest.fixture(params=["memory", "file", "encrypted"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryTokenStore()
    return FileTokenStore(tmp_path / "tokens.json", KEY if request.param == "encrypted" else None)


@pytest.mark.asyncio
async def test_roundtrip_expired_record_and_management(store):
    original = token()
    assert is_token_expired(original)
    await store.save_token("issuer:client:alice:resource", original)
    await store.save_token("bob", token(access_token="other"))
    result = await store.get_token("issuer:client:alice:resource")
    assert result == original
    assert result.to_dict() == original.to_dict()
    assert result.refresh_token == REFRESH
    with pytest.raises(FrozenInstanceError):
        result.access_token = "changed"
    assert set(await store.list_keys()) == {"issuer:client:alice:resource", "bob"}
    await store.delete_token("bob")
    await store.delete_token("missing")
    assert await store.get_token("bob") is None
    await store.clear()
    assert await store.list_keys() == []


@pytest.mark.asyncio
async def test_store_and_record_representations_are_safe(store, caplog, capsys):
    original = token()
    await store.save_token("key", original)
    caplog.set_level("DEBUG")
    import logging
    logging.getLogger(__name__).debug("store=%r token=%r", store, original)
    output = repr(original) + str(original) + repr(store) + caplog.text + str(capsys.readouterr())
    assert all(secret not in output for secret in (ACCESS, REFRESH, KEY))


@pytest.mark.asyncio
async def test_file_reopening_and_permissions(tmp_path, monkeypatch):
    path = tmp_path / "private" / "tokens.json"
    store = FileTokenStore(path)
    replace = os.replace
    modes = []

    def observe(source, destination):
        modes.append(stat.S_IMODE(Path(source).stat().st_mode))
        replace(source, destination)

    monkeypatch.setattr(os, "replace", observe)
    await store.save_token("alice", token())
    assert await FileTokenStore(path).get_token("alice") == token()
    assert list(path.parent.glob(".oauth-token-*")) == []
    if os.name == "posix":
        assert modes == [0o600]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        path.chmod(0o644)
        await store.get_token("alice")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["replace", "fsync", "serialization"])
async def test_atomic_failure_preserves_old_file(tmp_path, monkeypatch, failure_point):
    path = tmp_path / "tokens.json"
    store = FileTokenStore(path)
    await store.save_token("alice", token())
    original = path.read_bytes()

    def fail(*args):
        raise PermissionError(ACCESS + REFRESH)

    if failure_point == "serialization":
        monkeypatch.setattr(store, "_encrypt", fail)
    else:
        monkeypatch.setattr(os, failure_point, fail)
    with pytest.raises(TokenStoreError) as exc:
        await store.save_token("bob", token(access_token="replacement"))
    assert ACCESS not in str(exc.value) and REFRESH not in repr(exc.value)
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".oauth-token-*")) == []
    assert await store.get_token("alice") == token()


@pytest.mark.asyncio
async def test_concurrent_instances_do_not_lose_keys(tmp_path):
    stores = [FileTokenStore(tmp_path / "tokens.json") for _ in range(2)]
    await asyncio.gather(*(stores[i % 2].save_token(str(i), token(access_token=str(i))) for i in range(30)))
    assert set(await stores[0].list_keys()) == {str(i) for i in range(30)}
    assert [(await stores[1].get_token(str(i))).access_token for i in range(30)] == [str(i) for i in range(30)]


@pytest.mark.asyncio
async def test_legacy_encryption_format_and_wrong_key(tmp_path):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    path = tmp_path / "tokens.json"
    derived = hashlib.pbkdf2_hmac("sha256", KEY.encode(), b"nitrostack-token-store", 100000, 32)
    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(derived), modes.GCM(iv)).encryptor()
    ciphertext = encryptor.update(json.dumps({"alice": token().to_dict()}).encode()) + encryptor.finalize()
    path.write_text(f"{iv.hex()}:{encryptor.tag.hex()}:{ciphertext.hex()}")
    assert await FileTokenStore(path, KEY).get_token("alice") == token()
    original = path.read_bytes()
    with pytest.raises(TokenStoreError) as exc:
        await FileTokenStore(path, "wrong").get_token("alice")
    assert ACCESS not in str(exc.value)
    assert path.read_bytes() == original
    await FileTokenStore(path, KEY).save_token("bob", token())
    assert ACCESS not in path.read_text() and REFRESH not in path.read_text()
    assert await FileTokenStore(path, KEY).get_token("bob") == token()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["broken", "[]", '{"alice":null}', '{"alice":{"access_token":"secret"}}'])
async def test_corruption_is_an_error_not_an_empty_store(tmp_path, content):
    path = tmp_path / "tokens.json"
    path.write_text(content)
    with pytest.raises(TokenStoreError):
        await FileTokenStore(path).get_token("alice")
    assert path.read_text() == content


@pytest.mark.asyncio
async def test_missing_file_is_empty_and_symlink_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    store = FileTokenStore(path)
    assert await store.list_keys() == []
    assert not path.exists()
    if os.name == "posix":
        target = tmp_path / "target.json"
        target.write_text("{}")
        path.symlink_to(target)
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda value: value == path)
    with pytest.raises(TokenStoreError, match="symlink"):
        await store.save_token("alice", token())


@pytest.mark.asyncio
async def test_optional_encryption_import_is_lazy(tmp_path, monkeypatch):
    original = builtins.__import__

    def deny(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError(ACCESS)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    await FileTokenStore(tmp_path / "plain.json").save_token("a", token())
    with pytest.raises(TokenStoreError, match="token-encryption"):
        await FileTokenStore(tmp_path / "encrypted.json", KEY).save_token("a", token())
    assert not (tmp_path / "encrypted.json").exists()


def test_expiry_units_boundaries_rotation_and_one_hour_fallback(monkeypatch):
    monkeypatch.setattr("nitrostack.auth.token_store.time.time", lambda: 1000)
    assert calculate_expiration(60) == 1060000
    assert not is_token_expired(token(expires_at=1030001))
    assert is_token_expired(token(expires_at=1030000))
    assert is_token_expired(token(expires_at=1000000), skew=0)
    for response in [{"access_token": ACCESS}, {"access_token": ACCESS, "expires_in": None}]:
        assert token_response_to_stored(response).expires_at == 4600000
    assert token_response_to_stored({"access_token": ACCESS, "expires_in": 0}).expires_at == 1000000
    refreshed = token_response_to_stored({"access_token": "new"}, previous=token())
    assert refreshed.refresh_token == REFRESH and refreshed.scope == "read"
    assert refreshed.resource == token().resource


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan"), "60", None, True, 10 ** 400, 1e308],
                         ids=["negative", "infinite", "nan", "text", "none", "bool", "huge-int", "huge-float"])
def test_invalid_durations_are_rejected(duration):
    with pytest.raises(ValueError):
        calculate_expiration(duration)


def test_default_store_helpers_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Path(get_default_store_path()) == tmp_path / ".nitrostack" / "tokens.json"
    assert isinstance(create_default_token_store(), FileTokenStore)
    assert not (tmp_path / ".nitrostack").exists()
