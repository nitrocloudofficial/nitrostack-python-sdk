"""Exercise the urllib seam, DNS pins, TLS identity, and bounded IO."""

import asyncio
import io
import ssl
import threading
import traceback
import urllib.error
import urllib.request
from unittest.mock import AsyncMock, MagicMock

import pytest

from nitrostack import OAuth2Client, OAuthClientError
from nitrostack.auth import cimd
from nitrostack.auth.client import _Response

ENDPOINT = "https://issuer.example:8443/token"
SECRET = "TRANSPORT_SENTINEL_DO_NOT_LOG"


class WireResponse(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.code = status
        self.headers = headers or {}


def make_client(**limits):
    return OAuth2Client({"client_id": "client", "client_secret": SECRET, "token_endpoint": ENDPOINT}, **limits)


@pytest.mark.asyncio
async def test_real_urllib_seam_carries_form_headers_and_timeout(monkeypatch):
    oauth = make_client(timeout_seconds=4)
    opener = MagicMock()
    opener.open.return_value = WireResponse(b'{"access_token":"ok"}')
    builder = MagicMock(return_value=opener)
    monkeypatch.setattr(cimd, "_build_fetch_opener", builder)
    result = await oauth.get_client_credentials_token("alice")
    assert result.access_token == "ok"
    outgoing = opener.open.call_args.args[0]
    assert outgoing.full_url == ENDPOINT and outgoing.get_method() == "POST"
    assert outgoing.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert outgoing.get_header("Authorization").startswith("Basic ")
    assert opener.open.call_args.kwargs["timeout"] == 4
    builder.assert_called_once_with(ENDPOINT, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("body,headers", [
    (b"x" * 33, {}), (b"{}", {"Content-Length": "33"}),
    (b"{}", {"Content-Length": "-1"}), (b"{}", {"Content-Length": "not-a-number"}),
])
async def test_urllib_limits_response_before_parsing(monkeypatch, body, headers):
    oauth = make_client(max_response_bytes=32)
    opener = MagicMock()
    opener.open.return_value = WireResponse(body, headers=headers)
    monkeypatch.setattr(cimd, "_build_fetch_opener", lambda *args: opener)
    with pytest.raises(OAuthClientError):
        await oauth.get_client_credentials_token("alice")
    assert await oauth.store.list_keys() == []


@pytest.mark.asyncio
async def test_async_fake_transport_cannot_bypass_response_limit(monkeypatch):
    oauth = make_client(max_response_bytes=32)
    monkeypatch.setattr(oauth, "_send", AsyncMock(return_value=_Response(200, {}, b"x" * 33)))
    with pytest.raises(OAuthClientError, match="response size"):
        await oauth.get_client_credentials_token("alice")


@pytest.mark.asyncio
async def test_blocking_transport_runs_on_worker_thread(monkeypatch):
    oauth = make_client()
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()
    thread_ids = []

    def blocking(request):
        thread_ids.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return _Response(200, {}, b'{"access_token":"ok"}')

    monkeypatch.setattr(oauth, "_send_sync", blocking)
    task = asyncio.create_task(oauth.get_client_credentials_token("alice"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert thread_ids == [thread_ids[0]] and thread_ids[0] != threading.get_ident()
    finally:
        release.set()
    await task


@pytest.mark.asyncio
async def test_timeout_and_http_error_bodies_are_sanitized(monkeypatch):
    oauth = make_client()
    opener = MagicMock()
    monkeypatch.setattr(cimd, "_build_fetch_opener", lambda *args: opener)
    for failure in [TimeoutError(SECRET), urllib.error.HTTPError(
        ENDPOINT + "?token=" + SECRET, 401, SECRET, {},
        io.BytesIO(b'{"error":"invalid_client","error_description":"' + SECRET.encode() + b'"}'),
    )]:
        opener.open.side_effect = failure
        with pytest.raises(OAuthClientError) as exc:
            await oauth.get_client_credentials_token("alice")
        rendered = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
        assert SECRET not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_responses_are_terminal_not_followed(monkeypatch, status):
    oauth = make_client()
    send = AsyncMock(return_value=_Response(status, {"Location": "http://127.0.0.1/private"}, b""))
    monkeypatch.setattr(oauth, "_send", send)
    with pytest.raises(OAuthClientError) as exc:
        await oauth.get_client_credentials_token("alice")
    assert exc.value.status == status and send.await_count == 1
    request = urllib.request.Request(ENDPOINT)
    with pytest.raises(urllib.error.HTTPError):
        cimd._NoRedirectHandler().redirect_request(request, None, status, "redirect", {}, "http://127.0.0.1/private")


@pytest.mark.asyncio
async def test_untrusted_target_uses_checked_ip_and_disables_proxy_redirection(monkeypatch):
    oauth = OAuth2Client({"client_id": "client", "client_secret": SECRET})
    oauth._metadata = {"token_endpoint": ENDPOINT}
    guard = AsyncMock(return_value="8.8.8.8")
    monkeypatch.setattr(cimd, "assert_safe_fetch_target", guard)
    send = AsyncMock(return_value=_Response(200, {}, b'{"access_token":"ok"}'))
    monkeypatch.setattr(oauth, "_send", send)
    await oauth.get_client_credentials_token("alice")
    guard.assert_awaited_once_with(ENDPOINT, require_non_root_path=False)
    assert send.call_args.args[0].pinned_ip == "8.8.8.8"
    builder = MagicMock()
    monkeypatch.setattr(urllib.request, "build_opener", builder)
    cimd._build_fetch_opener(ENDPOINT, "8.8.8.8")
    handlers = builder.call_args.args
    assert any(isinstance(handler, cimd._NoRedirectHandler) for handler in handlers)
    assert any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers)
    secure = next(handler for handler in handlers if isinstance(handler, cimd._PinnedHTTPSHandler))
    assert secure._hostname == "issuer.example" and secure._port == 8443


def test_pinned_tls_connection_keeps_port_and_hostname(monkeypatch):
    sock = MagicMock()
    sock.getpeername.return_value = ("8.8.8.8", 8443)
    connect = MagicMock(return_value=sock)
    monkeypatch.setattr(cimd.socket, "create_connection", connect)
    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    wrap = MagicMock(return_value=sock)
    monkeypatch.setattr(context, "wrap_socket", wrap)
    connection = cimd._PinnedHTTPSConnection("issuer.example", "8.8.8.8", port=8443, context=context)
    connection.connect()
    assert connect.call_args.args[0] == ("8.8.8.8", 8443)
    wrap.assert_called_once_with(sock, server_hostname="issuer.example")
    sock.getpeername.return_value = ("127.0.0.1", 8443)
    with pytest.raises(cimd.CimdFetchError):
        connection.connect()
    assert sock.close.called


@pytest.mark.asyncio
async def test_generic_root_url_validation_does_not_weaken_cimd(monkeypatch):
    url = "https://public.example/"
    assert cimd.validate_fetch_url(url) == url
    with pytest.raises(cimd.CimdValidationError, match="non-root"):
        cimd.validate_client_identifier_url(url)
    with pytest.raises(cimd.CimdValidationError, match="non-root"):
        await cimd.assert_safe_fetch_target(url)
    monkeypatch.setattr(cimd.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 0))])
    assert await cimd.assert_safe_fetch_target(url, require_non_root_path=False) == "8.8.8.8"
    with pytest.raises(cimd.CimdValidationError):
        cimd.validate_fetch_url("https://public.example/%2e%2e/private")
