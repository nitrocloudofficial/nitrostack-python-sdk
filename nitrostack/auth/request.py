"""Resolve handler identity from HTTP headers and the request envelope.

Precedence (first present token is the only candidate):
1. HTTP ``Authorization`` Bearer
2. Spec envelope ``io.modelcontextprotocol/auth`` token
3. ``_meta.authorization`` Bearer (transports without HTTP headers)

A present token that fails verification yields empty identity. Unsigned
``userId`` / ``tenantId`` fields are never used.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from nitrostack.core.context import AuthContext
from nitrostack.protocol.meta import MCP_META_PREFIX, flatten_request_meta_object


def bearer_token_from_header(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith("Bearer "):
        token = stripped[len("Bearer ") :].strip()
        return token or None
    return None


def _header_value(headers: Mapping[str, str], name: str) -> Optional[str]:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def envelope_auth_slot(raw_meta: Mapping[str, Any] | None) -> Any:
    """Return the spec auth slot. Identity fields inside it are not trusted."""
    if not raw_meta:
        return None
    prefixed = f"{MCP_META_PREFIX}auth"
    if prefixed in raw_meta:
        return raw_meta[prefixed]
    return raw_meta.get("auth")


def bearer_token_from_envelope_auth(auth_slot: Any) -> Optional[str]:
    """Extract a Bearer or explicit token from the spec auth slot only."""
    if isinstance(auth_slot, str):
        return bearer_token_from_header(auth_slot)
    if not isinstance(auth_slot, dict):
        return None
    header = auth_slot.get("authorization") or auth_slot.get("Authorization")
    token = bearer_token_from_header(header)
    if token:
        return token
    for key in ("token", "accessToken"):
        value = auth_slot.get(key)
        if isinstance(value, str) and value.strip():
            return bearer_token_from_header(value) or value.strip()
    return None


def authorization_token_from_headers(headers: Mapping[str, str] | None) -> Optional[str]:
    if not headers:
        return None
    return bearer_token_from_header(
        _header_value(headers, "authorization") or _header_value(headers, "Authorization")
    )


def authorization_token_from_meta(raw_meta: Mapping[str, Any] | None) -> Optional[str]:
    if not raw_meta:
        return None
    token = bearer_token_from_envelope_auth(envelope_auth_slot(raw_meta))
    if token:
        return token
    token = bearer_token_from_header(
        raw_meta.get("authorization") or raw_meta.get("Authorization")
    )
    if token:
        return token
    headers = raw_meta.get("headers")
    if isinstance(headers, dict):
        return bearer_token_from_header(
            headers.get("authorization") or headers.get("Authorization")
        )
    return None


def authorization_token_from_request(rc: Any) -> Optional[str]:
    """Return the chosen Bearer token, or None when no candidate is present."""
    if rc is None:
        return None
    request = getattr(rc, "request", None)
    headers_obj = getattr(request, "headers", None) if request is not None else None
    if headers_obj is not None:
        try:
            header_map = {str(key): str(value) for key, value in headers_obj.items()}
        except Exception:
            header_map = {}
        header_token = authorization_token_from_headers(header_map)
        if header_token:
            return header_token

    return authorization_token_from_meta(flatten_request_meta_object(getattr(rc, "meta", None)))


def verify_bearer_payload(token: str) -> Optional[dict[str, Any]]:
    try:
        from nitrostack.auth.jwt import JWTService
        from nitrostack.core.di import DIContainer

        payload = DIContainer.get_instance().resolve(JWTService).verify_token(token)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def tenant_from_claims(claims: Mapping[str, Any]) -> Optional[str]:
    for key in ("tenant_id", "tenantId", "org_id", "orgId"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def auth_context_from_payload(payload: Mapping[str, Any]) -> AuthContext:
    raw_aud = payload.get("aud")
    aud = raw_aud if isinstance(raw_aud, list) else ([raw_aud] if raw_aud else None)
    scopes = payload.get("scopes") or payload.get("scope") or []
    if isinstance(scopes, str):
        scopes = [item for item in scopes.split(" ") if item]
    subject = payload.get("sub")
    return AuthContext(
        subject=subject if isinstance(subject, str) and subject.strip() else None,
        scopes=list(scopes) if isinstance(scopes, list) else [],
        client_id=payload.get("client_id"),
        exp=payload.get("exp"),
        iat=payload.get("iat"),
        iss=payload.get("iss"),
        aud=aud,
        claims=dict(payload),
        token_payload=dict(payload),
    )


def auth_context_from_request(rc: Any) -> Optional[AuthContext]:
    """Verified JWT identity, or None when missing or invalid."""
    token = authorization_token_from_request(rc)
    if not token:
        return None
    payload = verify_bearer_payload(token)
    if payload is None:
        return None
    return auth_context_from_payload(payload)
