"""
HTTP-facing OAuth discovery and registration document builders.

Separated from `oauth.py`'s `OAuthService` (token validation logic) so the shape of
each well-known document is a plain function you can call and assert on directly,
without spinning up the `http.server` thread `OAuthService.start_discovery_server()`
runs. `OAuthService` imports these and wires them into its `DiscoveryHandler`.

Three documents, three different jobs:
- RFC 8414 (`/.well-known/oauth-authorization-server`): "how do I talk to the
  authorization server?" — issuer, token/introspection endpoints, supported flows.
- RFC 9728 (`/.well-known/oauth-protected-resource`): "what does *this* resource
  server need, and which authorization server(s) does it trust?"
- RFC 7591 (`POST /oauth/v2/register`): Dynamic Client Registration — here, a
  simplified/static variant (see `build_registration_response`), matching the
  TypeScript SDK's behavior rather than full per-client credential issuance.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nitrostack.auth.oauth import OAuthService


def build_authorization_server_metadata(
    service: "OAuthService", registration_endpoint: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build an RFC 8414 Authorization Server Metadata document.

    nitrostack is a resource server, not the authorization server itself, so this
    document describes the *external* IdP configured via `authorization_servers`/
    `token_introspection_endpoint`/`jwks_uri` — it does not mean nitrostack serves
    these endpoints itself.
    """
    issuer = service.issuer or (
        service.authorization_servers[0] if service.authorization_servers else "http://localhost"
    )
    auth_server_base = service.authorization_servers[0] if service.authorization_servers else issuer

    metadata: Dict[str, Any] = {
        "issuer": issuer,
        "authorization_endpoint": f"{auth_server_base}/authorize",
        "token_endpoint": f"{auth_server_base}/token",
        "introspection_endpoint": service.token_introspection_endpoint or f"{auth_server_base}/introspect",
        "jwks_uri": service.jwks_uri or f"{auth_server_base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token"],
        "subject_types_supported": ["public"],
        "code_challenge_methods_supported": ["S256"],
    }
    if registration_endpoint:
        metadata["registration_endpoint"] = registration_endpoint
    return metadata


def build_protected_resource_metadata(service: "OAuthService") -> Dict[str, Any]:
    """Build an RFC 9728 Protected Resource Metadata document describing this server."""
    return {
        "resource": service.resource_uri,
        "authorization_servers": service.authorization_servers,
        "scopes_supported": service.scopes_supported,
        "bearer_methods_supported": ["header"],
    }


def is_client_registration_enabled(service: "OAuthService") -> bool:
    """
    Whether the static Dynamic Client Registration endpoint should be exposed.

    Requires BOTH an explicit opt-in (`enable_client_registration`, from config or
    `OAUTH_ENABLE_CLIENT_REGISTRATION=true`) AND a configured client id — never a
    literal default. Without a configured client id there is nothing to hand back.

    Deprecated on MCP 2026-07-28 in favor of Client ID Metadata Documents (CIMD).
    """
    return bool(service.enable_client_registration and service.static_client_id)


def build_registration_response(service: "OAuthService", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build an RFC 7591 client-registration response.

    This is the simplified, static-credential variant the TypeScript SDK ships:
    it always hands back the operator's own pre-configured client_id/client_secret
    rather than generating and storing new per-registration credentials. It exists
    only so MCP clients that require a `registration_endpoint` to be present don't
    refuse to proceed — not as a general-purpose multi-tenant registration service.
    """
    body = body or {}
    client_id = service.static_client_id
    client_secret = service.static_client_secret or ""
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(time.time()),
        "client_secret_expires_at": 0,  # never expires
        "grant_types": body.get("grant_types") or ["authorization_code", "refresh_token"],
        "response_types": body.get("response_types") or ["code"],
        # 'none' = public client authenticating via PKCE instead of a client secret
        # (the standard OAuth 2.1 pattern for CLI/desktop apps that can't hold a secret).
        "token_endpoint_auth_method": body.get("token_endpoint_auth_method")
        or ("client_secret_post" if client_secret else "none"),
        "redirect_uris": body.get("redirect_uris") or [],
    }
