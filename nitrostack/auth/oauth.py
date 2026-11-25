import os
import sys
import urllib.parse  # noqa: F401  (kept: callers patch nitrostack.auth.oauth.urllib.request)
import urllib.request  # noqa: F401
from typing import List, Optional, Dict, Any, Tuple, Union
from http.server import HTTPServer
import threading
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer
from nitrostack.auth.secret import SecretValue
from nitrostack.auth.oauth_types import (
    TokenInfo,
    _has_secret,
    generate_www_authenticate_header,
    is_oauth_required,
)
from nitrostack.auth.oauth_cache import cache_get, cache_set
from nitrostack.auth.oauth_tokens import (
    get_jwks_client,
    introspect_via_endpoint,
    introspect_via_jwks,
    validate_audience,
)
from nitrostack.auth.oauth_discovery import start_discovery_server
# Re-exported only so existing ``from nitrostack.auth.oauth import ...`` lines keep working.
from nitrostack.auth.secret import unwrap_secret  # noqa: F401
from nitrostack.auth.cimd import CimdFetchError, CimdValidationError  # noqa: F401
from nitrostack.auth.oauth_module import (  # noqa: F401
    apply_cimd_to_registration_body,
    build_authorization_server_metadata,
    build_protected_resource_metadata,
    build_registration_response,
    is_client_registration_enabled,
)
from nitrostack.core.errors import AudienceMismatchError, ConfigurationError, TokenInactiveError

# Everything that used to live in this module is still importable from it. The
# implementation is split by responsibility: oauth_types (helpers), oauth_cache,
# oauth_tokens (validation), oauth_discovery (metadata server). This module keeps
# OAuthService, OAuthModule and the fail-open warning state.
__all__ = [
    "OAuthModule",
    "OAuthService",
    "generate_www_authenticate_header",
    "is_oauth_required",
    "warn_if_oauth_fail_open",
]

_oauth_fail_open_warned = False


def warn_if_oauth_fail_open() -> None:
    """Loud warning when OAuth is wired but tokens are not enforced."""
    global _oauth_fail_open_warned
    if _oauth_fail_open_warned or is_oauth_required():
        return
    _oauth_fail_open_warned = True
    sys.stderr.write(
        "WARNING: OAuth is configured but OAUTH_REQUIRED is not true. "
        "Tools protected by OAuthGuard will accept unauthenticated requests. "
        "Set OAUTH_REQUIRED=true to enforce Bearer tokens.\n"
    )
    sys.stderr.flush()


class OAuthService:
    def __init__(
        self,
        resource_uri: str,
        authorization_servers: List[str],
        scopes_supported: List[str],
        token_introspection_endpoint: Optional[str] = None,
        token_introspection_client_id: Optional[str] = None,
        token_introspection_client_secret: Union[SecretValue, str, None] = None,
        discovery_port: int = 3005,
        jwks_uri: Optional[str] = None,
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        token_cache_seconds: Optional[int] = None,
        enable_client_registration: Optional[bool] = None,
        static_client_id: Optional[str] = None,
        static_client_secret: Union[SecretValue, str, None] = None,
    ):
        self.resource_uri = resource_uri
        self.authorization_servers = authorization_servers
        self.scopes_supported = scopes_supported
        self.discovery_port = discovery_port

        # Introspection settings fall back to the environment when not passed
        # explicitly. Both spellings of the endpoint variable are accepted: the
        # setup docs (OAUTH_SETUP.md, the CLI's generated guide, and the flight
        # booking example) all document `OAUTH_INTROSPECTION_ENDPOINT`, while the
        # generated app modules read `INTROSPECTION_ENDPOINT` -- so following the
        # documentation used to leave introspection silently unconfigured.
        # Resolving both here fixes it for every caller at once, including app
        # modules already written against either name. The TypeScript SDK reads
        # both variables too.
        self.token_introspection_endpoint = (
            token_introspection_endpoint
            or os.environ.get("OAUTH_INTROSPECTION_ENDPOINT")
            or os.environ.get("INTROSPECTION_ENDPOINT")
        )
        self.token_introspection_client_id = (
            token_introspection_client_id or os.environ.get("INTROSPECTION_CLIENT_ID")
        )
        self.token_introspection_client_secret = (
            token_introspection_client_secret
            if _has_secret(token_introspection_client_secret)
            else os.environ.get("INTROSPECTION_CLIENT_SECRET")
        )

        # Environmental fallbacks
        self.jwks_uri = jwks_uri or os.environ.get("JWKS_URI")
        self.audience = audience or os.environ.get("TOKEN_AUDIENCE") or resource_uri
        self.issuer = issuer or os.environ.get("TOKEN_ISSUER")
        self.token_cache_seconds = (
            token_cache_seconds
            if token_cache_seconds is not None
            else int(os.environ.get("OAUTH_TOKEN_CACHE_SECONDS", "300"))
        )

        # Dynamic Client Registration (RFC 7591) — see oauth_module.py for why this is
        # a simplified, static-credential variant rather than full per-client storage.
        self.enable_client_registration = (
            enable_client_registration
            if enable_client_registration is not None
            else os.environ.get("OAUTH_ENABLE_CLIENT_REGISTRATION", "").lower() == "true"
        )
        self.static_client_id = static_client_id or os.environ.get("OAUTH_CLIENT_ID")
        self.static_client_secret = (
            static_client_secret
            if _has_secret(static_client_secret)
            else os.environ.get("OAUTH_CLIENT_SECRET")
        )

        # Caches: JWKS client objects (keyed by jwks_uri — PyJWKClient already does its
        # own internal signing-key caching, this just avoids reconstructing the client
        # itself on every call) and introspection *results* (keyed by token, so repeated
        # calls with the same token skip both the HTTP round-trip and JWT verification).
        self._jwks_clients: Dict[str, Any] = {}
        self._token_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start_discovery_server(self) -> None:
        """Starts a background HTTP discovery server for OAuth Protected Resource metadata."""
        start_discovery_server(self)

    def raise_if_invalid(self, token_info: Dict[str, Any]) -> Dict[str, Any]:
        """Raise ``TokenInactiveError`` / ``AudienceMismatchError`` for an introspection result."""
        if token_info.get("error") == "audience_mismatch" or (
            token_info.get("active") and not self._validate_audience(token_info)
        ):
            raise AudienceMismatchError(self.audience, token_info.get("aud") or token_info.get("resource"))
        if not token_info.get("active"):
            raise TokenInactiveError()
        return token_info

    def stop_discovery_server(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    async def introspect_token(self, token: str) -> Dict[str, Any]:
        """
        Validate a Bearer token and return its introspection result (RFC 7662 shape:
        {"active": bool, ...claims}).

        Checks, in order:
        1. Cache (a prior successful result for this exact token, still within TTL).
        2. Token introspection endpoint (RFC 7662), if configured.
        3. JWKS/JWT signature verification, if configured.
        4. Neither configured -> {"active": False}. There is deliberately no "assume
           valid" fallback: a server that hasn't been told how to validate tokens
           must reject them, not accept everything. (This mirrors the TypeScript SDK,
           which has no such fallback either.)

        Every successful result is passed through `_validate_audience` (RFC 8707)
        before being trusted or cached — a token that's valid but wasn't issued for
        this resource is still rejected.
        """
        cached = self._cache_get(token)
        if cached is not None:
            return cached

        if self.token_introspection_endpoint:
            result = await self._introspect_via_endpoint(token)
        elif self.jwks_uri:
            result = self._introspect_via_jwks(token)
        else:
            return {"active": False}

        if result.get("active") and not self._validate_audience(result):
            sys.stderr.write(
                f"OAuth: token rejected, audience mismatch (expected {self.audience!r})\n"
            )
            sys.stderr.flush()
            result = {"active": False}

        if result.get("active"):
            self._cache_set(token, result)
        return result

    def _introspect_via_jwks(self, token: str) -> Dict[str, Any]:
        """JWT signature verification using a cached JWKS client (RFC 7517/7519)."""
        return introspect_via_jwks(self, token)

    async def _introspect_via_endpoint(self, token: str) -> Dict[str, Any]:
        """RFC 7662 token introspection: POST the token to the authorization server
        and ask whether it's active."""
        return await introspect_via_endpoint(self, token)

    def _get_jwks_client(self, jwks_uri: str):
        """Return a cached PyJWKClient for this URI, creating it on first use."""
        return get_jwks_client(self, jwks_uri)

    def _validate_audience(self, introspection: Dict[str, Any]) -> bool:
        """RFC 8707 resource-indicator check on the token's ``aud`` claim."""
        return validate_audience(self, introspection)

    def _cache_get(self, token: str) -> Optional[Dict[str, Any]]:
        return cache_get(self._token_cache, token)

    def _cache_set(self, token: str, result: Dict[str, Any]) -> None:
        cache_set(self._token_cache, token, result, self.token_cache_seconds)


@module(name="OAuthModule")
class OAuthModule:
    @classmethod
    def for_root(
        cls,
        resource_uri: str,
        authorization_servers: List[str],
        scopes_supported: List[str],
        token_introspection_endpoint: Optional[str] = None,
        token_introspection_client_id: Optional[str] = None,
        token_introspection_client_secret: Union[SecretValue, str, None] = None,
        discovery_port: int = 3005,
        jwks_uri: Optional[str] = None,
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        token_cache_seconds: Optional[int] = None,
        enable_client_registration: Optional[bool] = None,
        static_client_id: Optional[str] = None,
        static_client_secret: Union[SecretValue, str, None] = None,
    ):
        if not resource_uri or not str(resource_uri).strip():
            raise ConfigurationError("OAuthModule.for_root requires resource_uri")
        if not authorization_servers:
            raise ConfigurationError(
                "OAuthModule.for_root requires at least one authorization server"
            )
        service = OAuthService(
            resource_uri=resource_uri,
            authorization_servers=authorization_servers,
            scopes_supported=scopes_supported,
            token_introspection_endpoint=token_introspection_endpoint,
            token_introspection_client_id=token_introspection_client_id,
            token_introspection_client_secret=token_introspection_client_secret,
            discovery_port=discovery_port,
            jwks_uri=jwks_uri,
            audience=audience,
            issuer=issuer,
            token_cache_seconds=token_cache_seconds,
            enable_client_registration=enable_client_registration,
            static_client_id=static_client_id,
            static_client_secret=static_client_secret,
        )
        DIContainer.get_instance().register_value(OAuthService, service)
        warn_if_oauth_fail_open()
        return cls

    @staticmethod
    def is_auth_required() -> bool:
        return is_oauth_required()
