"""OAuth usability, revocation fallback, and automatic refresh regressions."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from nitrostack import OAuthClientError, StoredToken
from tests.support.oauth_client import (
    ACCESS, REFRESH, SECRET, client, install_transport, response, saved,
)


@pytest.mark.asyncio
async def test_secret_wrapper_deferred_unwrap(monkeypatch, caplog, capsys):
    import pickle
    from nitrostack import SecretValue
    secret = SecretValue(SECRET)
    original = SecretValue.unwrap
    calls = []
    def unwrap(self):
        calls.append(1)
        return original(self)
    monkeypatch.setattr(SecretValue, "unwrap", unwrap)
    oauth = client(client_secret=secret)
    assert oauth.config.client_secret is secret and calls == []
    send = install_transport(monkeypatch, oauth)
    await oauth.get_client_credentials_token("alice")
    assert len(calls) == 1
    plain = client()
    plain_send = install_transport(monkeypatch, plain)
    await plain.get_client_credentials_token("alice")
    assert send.call_args.args[0].headers == plain_send.call_args.args[0].headers
    for unusable in (pickle.loads(pickle.dumps(secret)),):
        broken = client(client_secret=unusable)
        transport = install_transport(monkeypatch, broken)
        with pytest.raises(OAuthClientError) as exc:
            await broken.get_client_credentials_token("alice")
        transport.assert_not_awaited()
        assert SECRET not in str(exc.value) + repr(exc.value)
    assert SECRET not in repr(oauth.config) + caplog.text + str(capsys.readouterr())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "http", "network"])
@pytest.mark.parametrize("fallback", [False, True])
async def test_local_revocation_fallback(monkeypatch, failure, fallback):
    oauth = client(revocation_endpoint=None) if failure == "missing" else client()
    previous = await saved(oauth)
    send = install_transport(monkeypatch, oauth, response({"error": "server_error"}, status=500))
    if failure == "network":
        send.side_effect = OSError(SECRET)
    if fallback:
        await oauth.revoke_token("alice", local_fallback=True)
        assert await oauth.store.get_token("alice") is None
    else:
        with pytest.raises(OAuthClientError):
            await oauth.revoke_token("alice")
        assert await oauth.store.get_token("alice") == previous
    assert send.await_count == (0 if failure == "missing" else 1)


@pytest.mark.asyncio
async def test_fallback_does_not_hide_validation_storage_or_cancellation(monkeypatch):
    oauth = client(revocation_endpoint=None)
    previous = await saved(oauth)
    with pytest.raises(OAuthClientError):
        await oauth.revoke_token("alice", token_type_hint="bad", local_fallback=True)
    assert await oauth.store.get_token("alice") == previous
    monkeypatch.setattr(oauth.store, "delete_token", AsyncMock(side_effect=OSError(SECRET)))
    with pytest.raises(OAuthClientError) as exc:
        await oauth.revoke_token("alice", local_fallback=True)
    assert SECRET not in str(exc.value)
    oauth = client()
    previous = await saved(oauth)
    send = install_transport(monkeypatch, oauth)
    send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await oauth.revoke_token("alice", local_fallback=True)
    assert await oauth.store.get_token("alice") == previous


@pytest.mark.asyncio
async def test_get_access_token_boundaries_and_explicit_refresh(monkeypatch):
    monkeypatch.setattr("nitrostack.auth.token_store.time.time", lambda: 1000)
    oauth = client()
    send = install_transport(monkeypatch, oauth)
    await oauth.store.save_token("alice", StoredToken(ACCESS, "Bearer", 1030001, REFRESH))
    assert await oauth.get_access_token("alice") == ACCESS
    send.assert_not_awaited()
    await oauth.refresh_token("alice")
    assert send.await_count == 1
    await oauth.store.save_token("alice", StoredToken(ACCESS, "Bearer", 1030000, REFRESH))
    await oauth.get_access_token("alice")
    assert send.await_count == 2
    with pytest.raises(OAuthClientError):
        await oauth.get_access_token("missing")
    await saved(oauth, refresh=None)
    with pytest.raises(OAuthClientError):
        await oauth.get_access_token("alice")


@pytest.mark.asyncio
async def test_get_access_token_coalesces_cancellation_and_delayed_read(monkeypatch):
    oauth = client()
    await saved(oauth)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def send(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return response({"access_token": "new", "refresh_token": "rotated"})
    monkeypatch.setattr(oauth, "_send", send)
    tasks = [asyncio.create_task(oauth.get_access_token("alice")) for _ in range(50)]
    await asyncio.wait_for(entered.wait(), 2)
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    release.set()
    assert await asyncio.gather(*tasks[1:]) == ["new"] * 49
    assert await oauth.get_access_token("alice") == "new"
    assert len(calls) == 1
    assert oauth._refresh_tasks == {} and oauth._force_refresh == {}
    assert (await oauth.store.get_token("alice")).refresh_token == "rotated"


@pytest.mark.asyncio
async def test_auto_refresh_rechecks_after_lock_and_explicit_call_forces(monkeypatch):
    oauth = client()
    send = install_transport(monkeypatch, oauth)
    await saved(oauth)
    lock = oauth._key_lock("alice")
    async with lock:
        pending = asyncio.create_task(oauth.get_access_token("alice"))
        await asyncio.sleep(0)
        await oauth.store.save_token("alice", StoredToken("fresh", "Bearer", 10**15, REFRESH))
    assert await pending == "fresh"
    send.assert_not_awaited()
    async with lock:
        automatic = asyncio.create_task(oauth.get_access_token("alice"))
        await asyncio.sleep(0)
        explicit = asyncio.create_task(oauth.refresh_token("alice"))
        await asyncio.sleep(0)
    await asyncio.gather(automatic, explicit)
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_get_access_token_failure_retry_and_persistence(monkeypatch):
    oauth = client()
    previous = await saved(oauth)
    install_transport(monkeypatch, oauth, response({"error": "invalid_grant"}, status=400))
    with pytest.raises(OAuthClientError):
        await oauth.get_access_token("alice")
    assert await oauth.store.get_token("alice") == previous
    send = install_transport(monkeypatch, oauth)
    monkeypatch.setattr(oauth.store, "save_token", AsyncMock(side_effect=OSError(SECRET)))
    with pytest.raises(OAuthClientError):
        await oauth.get_access_token("alice")
    assert send.await_count == 1 and await oauth.store.get_token("alice") == previous

@pytest.mark.asyncio
async def test_explicit_refresh_during_slow_valid_token_read(monkeypatch):
    oauth = client()
    await oauth.store.save_token("alice", StoredToken(ACCESS, "Bearer", 10**15, REFRESH))
    entered, release = asyncio.Event(), asyncio.Event()
    original = oauth.store.get_token
    async def slow_read(key):
        entered.set()
        await release.wait()
        return await original(key)
    monkeypatch.setattr(oauth.store, "get_token", slow_read)
    send = install_transport(monkeypatch, oauth)
    automatic = asyncio.create_task(oauth.get_access_token("alice"))
    await asyncio.wait_for(entered.wait(), 2)
    explicit = asyncio.create_task(oauth.refresh_token("alice"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(automatic, explicit)
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_local_fallback_waits_for_active_refresh(monkeypatch):
    oauth = client(revocation_endpoint=None)
    await saved(oauth)
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(request):
        entered.set()
        await release.wait()
        return response({"access_token": "rotated-access", "refresh_token": "rotated-refresh"})
    monkeypatch.setattr(oauth, "_send", send)
    pending = asyncio.create_task(oauth.get_access_token("alice"))
    await asyncio.wait_for(entered.wait(), 2)
    revoke = asyncio.create_task(oauth.revoke_token("alice", local_fallback=True))
    await asyncio.sleep(0)
    assert not revoke.done()
    release.set()
    await asyncio.gather(pending, revoke)
    assert await oauth.store.get_token("alice") is None


@pytest.mark.asyncio
async def test_helper_shared_failures_independent_keys_and_retry(monkeypatch):
    oauth = client()
    await saved(oauth)
    await saved(oauth, "bob")
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def send(request):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()
        return response({"error": "invalid_grant", "error_description": SECRET}, status=400)
    monkeypatch.setattr(oauth, "_send", send)
    tasks = [asyncio.create_task(oauth.get_access_token("alice")) for _ in range(20)]
    tasks.append(asyncio.create_task(oauth.get_access_token("bob")))
    await asyncio.wait_for(entered.wait(), 2)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert count == 2 and all(isinstance(result, OAuthClientError) for result in results)
    assert all(result is results[0] for result in results[:20])
    assert SECRET not in repr(results)
    assert oauth._refresh_tasks == {} and oauth._force_refresh == {}
    install_transport(monkeypatch, oauth)
    assert await oauth.get_access_token("alice") == ACCESS


@pytest.mark.asyncio
async def test_helper_rejects_immediately_expired_replacement_and_storage_failure(monkeypatch):
    oauth = client()
    await saved(oauth)
    install_transport(monkeypatch, oauth, response({"access_token": ACCESS, "expires_in": "0"}))
    with pytest.raises(OAuthClientError, match="expiry"):
        await oauth.get_access_token("alice")
    monkeypatch.setattr(oauth.store, "get_token", AsyncMock(side_effect=OSError(SECRET)))
    with pytest.raises(OAuthClientError) as exc:
        await oauth.get_access_token("alice")
    assert SECRET not in str(exc.value)
