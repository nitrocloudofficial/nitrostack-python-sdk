"""Token validation: JWKS verification, RFC 7662 introspection and audience checks.

Each function takes the ``OAuthService`` whose settings it uses. Depends only on
``oauth_types`` (and ``secret``); it never imports ``oauth``.
"""

import json
import sys
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from nitrostack.auth.oauth_types import TokenInfo, _has_secret
from nitrostack.auth.secret import unwrap_secret

if TYPE_CHECKING:
    from nitrostack.auth.oauth import OAuthService


def get_jwks_client(service: "OAuthService", jwks_uri: str) -> Any:
    """Return a cached PyJWKClient for this URI, creating it on first use.

    PyJWKClient already caches individual signing keys internally; this cache
    is one level up — it avoids reconstructing the client object itself (and
    re-fetching the whole key set) on every single token check.
    """
    import jwt
    client = service._jwks_clients.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri)
        service._jwks_clients[jwks_uri] = client
    return client


def introspect_via_jwks(service: "OAuthService", token: str) -> TokenInfo:
    """JWT signature verification using a cached JWKS client (RFC 7517/7519)."""
    try:
        import jwt
        jwks_client = service._get_jwks_client(service.jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=service.audience,
            issuer=service.issuer,
        )
        return {
            "active": True,
            "scope": data.get("scope", ""),
            "sub": data.get("sub"),
            "client_id": data.get("client_id"),
            "aud": data.get("aud"),
            "exp": data.get("exp"),
            "iat": data.get("iat"),
            "iss": data.get("iss"),
        }
    except Exception as e:
        sys.stderr.write(f"OAuth JWKS verification failed: {e}\n")
        sys.stderr.flush()
        return {"active": False}


async def introspect_via_endpoint(service: "OAuthService", token: str) -> TokenInfo:
    """RFC 7662 token introspection: POST the token to the authorization server
    and ask whether it's active."""
    data = urllib.parse.urlencode({"token": token}).encode("utf-8")
    req = urllib.request.Request(service.token_introspection_endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    # Add basic auth if client credentials provided
    if service.token_introspection_client_id and _has_secret(service.token_introspection_client_secret):
        import base64
        client_secret = unwrap_secret(service.token_introspection_client_secret)
        auth_str = f"{service.token_introspection_client_id}:{client_secret}"
        encoded_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        req.add_header("Authorization", f"Basic {encoded_auth}")

    try:
        # urllib.request is synchronous; run it on a thread so it doesn't block
        # the event loop this async method is running on.
        import asyncio
        loop = asyncio.get_event_loop()

        def do_request():
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        return await loop.run_in_executor(None, do_request)
    except Exception as e:
        sys.stderr.write(f"OAuth Introspection Request Failed: {e}\n")
        sys.stderr.flush()
        return {"active": False}


def validate_audience(service: "OAuthService", introspection: TokenInfo) -> bool:
    """
    RFC 8707 resource-indicator check: does this token's `aud` claim include
    the resource it's being presented to? Without this, a token minted for a
    *different* service could be replayed here and accepted — the token is
    legitimately signed/active, just not meant for this resource.

    `aud` is legal as either a single string or a list of strings per JWT
    conventions, so it's normalized to a list before comparing.
    """
    expected = service.audience or service.resource_uri
    if not expected:
        # Nothing configured to check against — permissive, matching the
        # TypeScript SDK's default when no audience is configured.
        return True

    raw_aud = introspection.get("aud")
    if raw_aud is None:
        # No audience claim on the token at all — nothing to validate against.
        # Treat as permissive rather than rejecting tokens from authorization
        # servers that don't emit `aud`.
        return True

    token_audiences = raw_aud if isinstance(raw_aud, list) else [raw_aud]
    return expected in token_audiences
