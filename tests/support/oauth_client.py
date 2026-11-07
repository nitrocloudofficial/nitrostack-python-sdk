"""Shared OAuth client fixtures without network access."""

import json
from unittest.mock import AsyncMock

from nitrostack import OAuth2Client, StoredToken
from nitrostack.auth.client import _Response

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
