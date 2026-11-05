"""Issue #68 client lifecycle, discovery trust, and single-flight refresh tests."""

import asyncio
import base64
import json
import logging
import traceback
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import AsyncMock

import pytest

from nitrostack import (
    AuthorizationFlow, ClientRegistration, FileTokenStore, MemoryTokenStore, OAuth2Client,
    OAuth2ClientConfig, OAuthClientError, StoredToken, get_well_known_metadata_uris,
    parse_www_authenticate_header,
)
from nitrostack.auth import cimd
from nitrostack.auth.client import _Response
from nitrostack.auth.pkce import generate_code_challenge, is_valid_code_verifier

ACCESS = "ACCESS_SENTINEL_NEVER_LOG"
REFRESH = "REFRESH_SENTINEL_NEVER_LOG"
SECRET = "CLIENT_SENTINEL_NEVER_LOG: with spaces"
ENDPOINT = "https://issuer.example/token"


def response(value=None, *, status=200, headers=None, raw=None):
    body = raw if raw is not None else json.dumps(value if value is not None else {"access_token": ACCESS}).encode()
    return _Response(status, headers or {}, body)


def client(**overrides):
    return OAuth2Client({"client_id": "client:id", "client_secret": SECRET,
                         "issuer": "https://issuer.example", "redirect_uri": "http://127.0.0.1/callback",
                         "authorization_endpoint": "https://issuer.example/authorize?audience=api&state=old&scope=old",
                         "token_endpoint": ENDPOINT, "revocation_endpoint": "https://issuer.example/revoke",
                         "registration_endpoint": "https://issuer.example/register", **overrides})


def install_transport(monkeypatch, oauth, *responses):
    send = AsyncMock(side_effect=list(responses) if responses else None)
    if not responses:
        send.return_value = response()
    monkeypatch.setattr(oauth, "_send", send)
    return send


async def saved(oauth, key="alice", refresh=REFRESH):
    token = StoredToken(ACCESS, "Bearer", 1, refresh, "read", "https://resource.example")
    await oauth.store.save_token(key, token)
    return token


@pytest.mark.asyncio
async def test_client_credentials_are_encoded_saved_and_redacted(monkeypatch):
    oauth = client()
    send = install_transport(monkeypatch, oauth, response({"access_token": ACCESS, "refresh_token": REFRESH}))
    result = await oauth.get_client_credentials_token("alice", scope="read", resource="https://resource.example")
    assert isinstance(result, StoredToken) and await oauth.store.get_token("alice") == result
    request = send.call_args.args[0]
    params = urllib.parse.parse_qs(request.body.decode())
    assert params == {"grant_type": ["client_credentials"], "client_id": ["client:id"],
                      "scope": ["read"], "resource": ["https://resource.example"]}
    assert base64.b64decode(request.headers["Authorization"][6:]).decode() == (
        urllib.parse.quote_plus("client:id", safe="") + ":" + urllib.parse.quote_plus(SECRET, safe="")
    )
    assert request.timeout == 10 and request.pinned_ip is None
    assert all(secret not in repr(request) + repr(result) + repr(oauth.config) for secret in (ACCESS, REFRESH, SECRET))


@pytest.mark.asyncio
@pytest.mark.parametrize("encryption_key", [None, "encryption-key"])
async def test_client_persists_and_refreshes_after_file_store_reopening(tmp_path, monkeypatch, encryption_key):
    path = tmp_path / "tokens.json"
    config = OAuth2ClientConfig(client_id="client", client_secret=SECRET, token_endpoint=ENDPOINT)
    oauth = OAuth2Client(config, store=FileTokenStore(path, encryption_key))
    install_transport(monkeypatch, oauth, response({"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 0}))
    await oauth.get_client_credentials_token("alice", scope="read", resource="https://resource.example")
    reopened = OAuth2Client(config, store=FileTokenStore(path, encryption_key))
    install_transport(monkeypatch, reopened, response({"access_token": "replacement"}))
    token = await reopened.refresh_token("alice")
    assert token.access_token == "replacement" and token.refresh_token == REFRESH
    assert token.resource == "https://resource.example"
    assert await reopened.store.get_token("alice") == token


@pytest.mark.asyncio
async def test_authorization_url_pkce_state_and_public_code_exchange(monkeypatch):
    oauth = client(client_secret=None)
    flow = await oauth.start_authorization_flow(scope="read", resource="https://resource.example")
    second = await oauth.start_authorization_flow()
    assert isinstance(flow, AuthorizationFlow) and flow.state != second.state
    assert is_valid_code_verifier(flow.code_verifier)
    assert generate_code_challenge(flow.code_verifier) == flow.code_challenge
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(flow.authorization_url).query)
    assert query["state"] == [flow.state] and query["audience"] == ["api"]
    assert query["code_challenge_method"] == ["S256"]
    assert flow.code_verifier not in flow.authorization_url
    assert "scope" not in urllib.parse.parse_qs(urllib.parse.urlsplit(second.authorization_url).query)
    assert flow.code_verifier not in repr(flow) and flow.state not in repr(flow)
    send = install_transport(monkeypatch, oauth)
    token = await oauth.exchange_code_for_token("alice", code="one-time-code", code_verifier=flow.code_verifier)
    request = send.call_args.args[0]
    assert "Authorization" not in request.headers
    assert urllib.parse.parse_qs(request.body.decode())["code_verifier"] == [flow.code_verifier]
    assert await oauth.store.get_token("alice") == token


@pytest.mark.asyncio
async def test_refresh_is_single_flight_and_preserves_rotation_fields(monkeypatch):
    oauth = client()
    previous = await saved(oauth)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return response({"access_token": "replacement"})

    monkeypatch.setattr(oauth, "_send", send)
    tasks = [asyncio.create_task(oauth.refresh_token("alice")) for _ in range(50)]
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(result is results[0] for result in results)
    assert results[0].access_token == "replacement"
    assert results[0].refresh_token == previous.refresh_token
    assert results[0].scope == previous.scope and results[0].resource == previous.resource
    assert await oauth.store.get_token("alice") == results[0]
    assert oauth._refresh_tasks == {}


@pytest.mark.asyncio
async def test_different_keys_refresh_independently_and_one_waiter_can_cancel(monkeypatch):
    oauth = client()
    await saved(oauth, "alice", "alice-refresh")
    await saved(oauth, "bob", "bob-refresh")
    both_started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(request):
        calls.append(urllib.parse.parse_qs(request.body.decode())["refresh_token"][0])
        if len(calls) == 2:
            both_started.set()
        await release.wait()
        return response()

    monkeypatch.setattr(oauth, "_send", send)
    cancelled = asyncio.create_task(oauth.refresh_token("alice"))
    survivor = asyncio.create_task(oauth.refresh_token("alice"))
    bob = asyncio.create_task(oauth.refresh_token("bob"))
    await asyncio.wait_for(both_started.wait(), 2)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not survivor.done()
    release.set()
    await asyncio.gather(survivor, bob)
    assert sorted(calls) == ["alice-refresh", "bob-refresh"]


@pytest.mark.asyncio
async def test_refresh_shared_failure_can_retry_without_losing_credentials(monkeypatch):
    oauth = client()
    previous = await saved(oauth)
    release = asyncio.Event()
    calls = []

    async def send(request):
        calls.append(request)
        await release.wait()
        return response({"error": "invalid_grant", "error_description": ACCESS + REFRESH}, status=400)

    monkeypatch.setattr(oauth, "_send", send)
    tasks = [asyncio.create_task(oauth.refresh_token("alice")) for _ in range(20)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    release.set()
    errors = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(calls) == 1 and all(isinstance(error, OAuthClientError) for error in errors)
    assert all(error is errors[0] for error in errors)
    assert "invalid_grant" in str(errors[0]) and ACCESS not in repr(errors[0])
    assert await oauth.store.get_token("alice") == previous
    send = install_transport(monkeypatch, oauth, response({"access_token": "new", "refresh_token": "rotated"}))
    result = await oauth.refresh_token("alice")
    assert send.await_count == 1 and result.refresh_token == "rotated"


@pytest.mark.asyncio
async def test_persistence_failure_does_not_claim_refresh_success(monkeypatch):
    oauth = client()
    previous = await saved(oauth)
    install_transport(monkeypatch, oauth)
    monkeypatch.setattr(oauth.store, "save_token", AsyncMock(side_effect=OSError(ACCESS)))
    with pytest.raises(OAuthClientError) as exc:
        await oauth.refresh_token("alice")
    assert ACCESS not in str(exc.value)
    assert await oauth.store.get_token("alice") == previous


@pytest.mark.asyncio
async def test_revocation_waits_for_refresh_and_removes_rotated_credential(monkeypatch):
    oauth = client()
    await saved(oauth)
    entered, release = asyncio.Event(), asyncio.Event()
    revoked = []

    async def send(request):
        if request.url == ENDPOINT:
            entered.set()
            await release.wait()
            return response({"access_token": "new-access", "refresh_token": "new-refresh"})
        revoked.append(urllib.parse.parse_qs(request.body.decode())["token"][0])
        return response(raw=b"")

    monkeypatch.setattr(oauth, "_send", send)
    refresh = asyncio.create_task(oauth.refresh_token("alice"))
    await asyncio.wait_for(entered.wait(), 2)
    revoke = asyncio.create_task(oauth.revoke_token("alice"))
    await asyncio.sleep(0)
    assert revoked == []
    release.set()
    await asyncio.gather(refresh, revoke)
    assert revoked == ["new-refresh"]
    assert await oauth.store.get_token("alice") is None


@pytest.mark.asyncio
async def test_all_waiters_can_cancel_without_leaking_unhandled_failures(monkeypatch):
    oauth = client()
    await saved(oauth)
    entered, release = asyncio.Event(), asyncio.Event()
    errors = []
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))

    async def send(request):
        entered.set()
        await release.wait()
        raise OSError(ACCESS)

    monkeypatch.setattr(oauth, "_send", send)
    try:
        waiter = asyncio.create_task(oauth.refresh_token("alice"))
        await asyncio.wait_for(entered.wait(), 2)
        shared = oauth._refresh_tasks["alice"]
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not shared.cancelled()
        release.set()
        with pytest.raises(OAuthClientError):
            await shared
        await asyncio.sleep(0)
        assert oauth._refresh_tasks == {} and errors == []
    finally:
        loop.set_exception_handler(original_handler)


@pytest.mark.asyncio
async def test_revocation_success_failure_and_registration(monkeypatch):
    oauth = client()
    await saved(oauth)
    send = install_transport(monkeypatch, oauth, response(raw=b"", status=200))
    await oauth.revoke_token("alice")
    assert await oauth.store.get_token("alice") is None
    assert urllib.parse.parse_qs(send.call_args.args[0].body.decode())["token"] == [REFRESH]
    await saved(oauth)
    install_transport(monkeypatch, oauth, response({"error": "invalid_client"}, status=401))
    with pytest.raises(OAuthClientError):
        await oauth.revoke_token("alice", token_type_hint="access_token")
    assert await oauth.store.get_token("alice") is not None
    send = install_transport(monkeypatch, oauth, response({"client_id": "registered", "client_secret": SECRET}))
    result = await oauth.register_client({"redirect_uris": ["http://127.0.0.1/callback"]})
    assert isinstance(result, ClientRegistration) and result.client_secret == SECRET
    assert SECRET not in repr(result)
    assert result.to_dict()["client_secret"] == SECRET
    assert json.loads(send.call_args.args[0].body)["redirect_uris"] == ["http://127.0.0.1/callback"]


@pytest.mark.asyncio
async def test_errors_logs_and_transport_representations_do_not_leak(monkeypatch, caplog, capsys):
    oauth = client()
    caplog.set_level(logging.DEBUG)
    send = install_transport(monkeypatch, oauth, response({"error": ACCESS, "error_description": SECRET}, status=401))
    with pytest.raises(OAuthClientError) as exc:
        await oauth.get_client_credentials_token("alice")
    rendered = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    logging.getLogger(__name__).debug("client=%r config=%r request=%r error=%r", oauth, oauth.config,
                                     send.call_args.args[0], exc.value)
    output = rendered + caplog.text + str(capsys.readouterr())
    assert all(secret not in output for secret in (ACCESS, REFRESH, SECRET))
    install_transport(monkeypatch, oauth, OSError(ACCESS + SECRET))
    with pytest.raises(OAuthClientError) as exc:
        await oauth.get_client_credentials_token("alice")
    assert all(secret not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb)) for secret in (ACCESS, SECRET))


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"not-json", b"[]", b"null", b'{}', b'{"access_token":null}',
                                  b'{"access_token":"token","expires_in":-1}'])
async def test_invalid_token_responses_are_not_saved(monkeypatch, raw):
    oauth = client()
    install_transport(monkeypatch, oauth, response(raw=raw))
    with pytest.raises(OAuthClientError):
        await oauth.get_client_credentials_token("alice")
    assert await oauth.store.list_keys() == []


@pytest.mark.asyncio
async def test_discovery_pins_unknown_urls_and_does_not_require_pkce_for_client_credentials(monkeypatch):
    oauth = OAuth2Client({"client_id": "client", "client_secret": SECRET, "issuer": "https://issuer.example"})
    safe = AsyncMock(return_value="8.8.8.8")
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", safe)
    metadata = {"issuer": "https://issuer.example", "token_endpoint": ENDPOINT,
                "authorization_endpoint": "https://issuer.example/authorize"}
    send = install_transport(monkeypatch, oauth, response(metadata), response())
    assert await oauth.discover_authorization_server_metadata() == metadata
    result = await oauth.get_client_credentials_token("alice")
    assert result.access_token == ACCESS
    assert safe.await_count == 2
    assert all(call.args[0].pinned_ip == "8.8.8.8" for call in send.call_args_list)
    with pytest.raises(OAuthClientError, match="PKCE"):
        await oauth.start_authorization_flow(redirect_uri="http://127.0.0.1/callback")


@pytest.mark.asyncio
async def test_discovery_failure_preserves_previous_metadata_and_explicit_endpoint_wins(monkeypatch):
    oauth = client()
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", AsyncMock(return_value="8.8.8.8"))
    valid = {"issuer": "https://issuer.example", "token_endpoint": "https://discovered.example/token"}
    send = install_transport(monkeypatch, oauth, response(valid))
    await oauth.discover_authorization_server_metadata()
    assert oauth._endpoint("token_endpoint") == ENDPOINT
    install_transport(monkeypatch, oauth, response({"issuer": "https://wrong.example"}),
                      response(raw=b"[]"))
    with pytest.raises(OAuthClientError):
        await oauth.discover_authorization_server_metadata()
    assert oauth._metadata == valid


@pytest.mark.asyncio
async def test_protected_resource_challenge_and_well_known_fallback(monkeypatch):
    oauth = client()
    safe = AsyncMock(return_value="8.8.8.8")
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", safe)
    resource = "https://resource.example/reports"
    metadata = {"resource": resource, "authorization_servers": ["https://issuer.example"]}
    send = install_transport(monkeypatch, oauth,
                             response({}, status=401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://metadata.example/info"'}),
                             response(metadata))
    assert await oauth.discover_protected_resource_metadata(resource) == metadata
    assert send.call_args_list[1].args[0].url == "https://metadata.example/info"
    send = install_transport(monkeypatch, oauth, response({}, status=404), response(metadata))
    assert await oauth.discover_protected_resource_metadata(resource) == metadata
    assert send.call_args_list[1].args[0].url.endswith("/.well-known/oauth-protected-resource/reports")


@pytest.mark.asyncio
async def test_operator_configured_loopback_metadata_avoids_ssrf_exception_flags(monkeypatch):
    issuer = "http://127.0.0.1:9876"
    oauth = OAuth2Client({"issuer": issuer, "token_endpoint": issuer + "/token",
                          "authorization_server_metadata_url": issuer + "/.well-known/oauth-authorization-server"})
    guard = AsyncMock(side_effect=AssertionError("must not fetch untrusted URLs"))
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", guard)
    send = install_transport(monkeypatch, oauth, response({"issuer": issuer, "token_endpoint": issuer + "/token"}))
    await oauth.discover_authorization_server_metadata()
    guard.assert_not_called()
    assert send.call_args.args[0].pinned_ip is None


@pytest.mark.asyncio
async def test_untrusted_private_dns_is_rejected_before_credentials_are_sent(monkeypatch):
    oauth = client(token_endpoint=None)
    oauth._metadata = {"token_endpoint": "https://internal.example/token"}
    monkeypatch.setattr(cimd.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("10.0.0.1", 0))])
    send = install_transport(monkeypatch, oauth)
    with pytest.raises(OAuthClientError):
        await oauth.get_client_credentials_token("alice")
    send.assert_not_called()


def test_metadata_helpers_and_snake_case_only():
    assert get_well_known_metadata_uris("https://resource.example/reports") == [
        "https://resource.example/.well-known/oauth-protected-resource/reports",
        "https://resource.example/.well-known/oauth-protected-resource",
    ]
    parsed = parse_www_authenticate_header('Basic realm="other", bEaReR realm="a,b", resource_metadata="https://safe.example/info"')
    assert parsed["realm"] == "a,b" and parsed["resource_metadata"] == "https://safe.example/info"
    assert parse_www_authenticate_header('Basic realm="other"') is None
    assert not hasattr(client(), "refreshToken")
    assert not hasattr(MemoryTokenStore(), "getToken")


@pytest.mark.asyncio
async def test_supplied_state_and_discovered_pkce_support(monkeypatch):
    oauth = client()
    oauth._metadata = {"code_challenge_methods_supported": ["S256"]}
    flow = await oauth.start_authorization_flow(state="caller-state")
    assert flow.state == "caller-state" and flow.code_challenge_method == "S256"
    with pytest.raises(OAuthClientError):
        await oauth.start_authorization_flow(state="")
    with pytest.raises(OAuthClientError):
        await oauth.exchange_code_for_token("alice", code="code", code_verifier="bad")
    with pytest.raises(OAuthClientError):
        await oauth.refresh_token("missing")
    with pytest.raises(OAuthClientError):
        await oauth.refresh_token("")
    assert isinstance(OAuth2Client({}).store, MemoryTokenStore)


@pytest.mark.asyncio
async def test_configured_protected_metadata_and_failed_document_do_not_leak(monkeypatch):
    metadata_url = "https://operator.example/resource-metadata"
    oauth = client(protected_resource_metadata_url=metadata_url)
    guard = AsyncMock(side_effect=AssertionError("operator endpoint must be trusted"))
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", guard)
    metadata = {"resource": "https://resource.example/", "authorization_servers": ["https://issuer.example"]}
    send = install_transport(monkeypatch, oauth, response(metadata))
    assert await oauth.discover_protected_resource_metadata("https://resource.example/") == metadata
    assert send.call_args.args[0].url == metadata_url
    guard.assert_not_called()
    install_transport(monkeypatch, oauth, response({"resource": "https://wrong.example/"}))
    with pytest.raises(OAuthClientError):
        await oauth.discover_protected_resource_metadata("https://resource.example/")


@pytest.mark.parametrize("options", [{"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
                                     {"max_response_bytes": 0}, {"max_response_bytes": True}])
def test_invalid_client_limits(options):
    with pytest.raises(OAuthClientError):
        OAuth2Client({}, **options)


@pytest.mark.parametrize("url", ["http://public.example/token", "https://user:secret@public.example/token",
                                 "https://public.example/token#fragment", "https://public.example:bad/token"])
def test_invalid_operator_url_is_rejected(url):
    with pytest.raises(OAuthClientError):
        client(token_endpoint=url)
