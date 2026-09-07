import os
import sys
import time
import json
import urllib.request
import urllib.parse
from typing import List, Optional, Dict, Any, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer
from nitrostack.auth.oauth_module import (
    apply_cimd_to_registration_body,
    build_authorization_server_metadata,
    build_protected_resource_metadata,
    build_registration_response,
    is_client_registration_enabled,
)
from nitrostack.auth.cimd import CimdFetchError, CimdValidationError
from nitrostack.core.errors import AudienceMismatchError, ConfigurationError, TokenInactiveError


def is_oauth_required() -> bool:
    """TS ``OAuthModule.isAuthRequired()`` — Studio/local default is off.

    Set ``OAUTH_REQUIRED=true`` to enforce Bearer tokens. Unset/false lets
    NitroStudio and Inspector call tools against mock Duffel data.
    """
    return (os.environ.get("OAUTH_REQUIRED") or "").strip().lower() == "true"


_oauth_fail_open_warned = False


def generate_www_authenticate_header(
    *,
    realm: str = "mcp",
    resource_metadata: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> str:
    """RFC 6750 / RFC 9728 WWW-Authenticate value for protected MCP resources."""
    parts = [f'Bearer realm="{realm}"']
    if resource_metadata:
        parts.append(f'resource_metadata="{resource_metadata}"')
    if error:
        parts.append(f'error="{error}"')
    if error_description:
        escaped = error_description.replace('"', "'")
        parts.append(f'error_description="{escaped}"')
    return ", ".join(parts)


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
        token_introspection_client_secret: Optional[str] = None,
        discovery_port: int = 3005,
        jwks_uri: Optional[str] = None,
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        token_cache_seconds: Optional[int] = None,
        enable_client_registration: Optional[bool] = None,
        static_client_id: Optional[str] = None,
        static_client_secret: Optional[str] = None,
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
            token_introspection_client_secret or os.environ.get("INTROSPECTION_CLIENT_SECRET")
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
        self.static_client_secret = static_client_secret or os.environ.get("OAUTH_CLIENT_SECRET")

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
        if self._server is not None:
            return

        service_instance = self
        registration_path = "/oauth/v2/register"

        def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(raw)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(raw)

        def _write_empty(handler: BaseHTTPRequestHandler, status: int) -> None:
            handler.send_response(status)
            handler.send_header("Content-Length", "0")
            handler.send_header("Connection", "close")
            handler.end_headers()

        class DiscoveryHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Suppress server logging to stdout/stderr to keep stdio clean
                pass

            def do_OPTIONS(self):
                # TS discovery handlers honor CORS preflight without leaking credentials.
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_GET(self):
                if self.path == "/.well-known/oauth-protected-resource":
                    _write_json(self, 200, build_protected_resource_metadata(service_instance))
                elif self.path == "/.well-known/oauth-authorization-server":
                    registration_endpoint = (
                        registration_path if is_client_registration_enabled(service_instance) else None
                    )
                    _write_json(
                        self,
                        200,
                        build_authorization_server_metadata(service_instance, registration_endpoint),
                    )
                else:
                    _write_empty(self, 404)

            def do_POST(self):
                if self.path != registration_path:
                    _write_empty(self, 404)
                    return

                if not is_client_registration_enabled(service_instance):
                    _write_json(
                        self,
                        404,
                        {"error": "not_found", "error_description": "Client registration is not enabled"},
                    )
                    return

                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                except Exception:
                    body = {}

                try:
                    body = apply_cimd_to_registration_body(body)
                except (CimdValidationError, CimdFetchError) as exc:
                    _write_json(
                        self,
                        400,
                        {
                            "error": "invalid_client_metadata",
                            "error_description": str(exc),
                        },
                    )
                    return

                _write_json(self, 200, build_registration_response(service_instance, body))

        def run_server():
            # Try binding to OAUTH_DISCOVERY_PORT
            port = int(os.environ.get("OAUTH_DISCOVERY_PORT", self.discovery_port))
            try:
                self._server = HTTPServer(("localhost", port), DiscoveryHandler)
                # Notify client via stderr
                oauth_metadata = {
                    "port": port,
                    "resource_uri": self.resource_uri,
                    "authorization_servers": self.authorization_servers
                }
                sys.stderr.write(
                    f"[NITROSTACK_OAUTH]{json.dumps(oauth_metadata)}[/NITROSTACK_OAUTH]\n"
                )
                sys.stderr.flush()
                self._server.serve_forever()
            except Exception as e:
                sys.stderr.write(f"Failed to start OAuth Discovery Server on port {port}: {e}\n")
                sys.stderr.flush()

        self._thread = threading.Thread(target=run_server, daemon=True)
        self._thread.start()

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
        try:
            import jwt
            jwks_client = self._get_jwks_client(self.jwks_uri)
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            data = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
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

    async def _introspect_via_endpoint(self, token: str) -> Dict[str, Any]:
        """RFC 7662 token introspection: POST the token to the authorization server
        and ask whether it's active."""
        data = urllib.parse.urlencode({"token": token}).encode("utf-8")
        req = urllib.request.Request(self.token_introspection_endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        # Add basic auth if client credentials provided
        if self.token_introspection_client_id and self.token_introspection_client_secret:
            import base64
            auth_str = f"{self.token_introspection_client_id}:{self.token_introspection_client_secret}"
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

    def _get_jwks_client(self, jwks_uri: str):
        """Return a cached PyJWKClient for this URI, creating it on first use.

        PyJWKClient already caches individual signing keys internally; this cache
        is one level up — it avoids reconstructing the client object itself (and
        re-fetching the whole key set) on every single token check.
        """
        import jwt
        client = self._jwks_clients.get(jwks_uri)
        if client is None:
            client = jwt.PyJWKClient(jwks_uri)
            self._jwks_clients[jwks_uri] = client
        return client

    def _validate_audience(self, introspection: Dict[str, Any]) -> bool:
        """
        RFC 8707 resource-indicator check: does this token's `aud` claim include
        the resource it's being presented to? Without this, a token minted for a
        *different* service could be replayed here and accepted — the token is
        legitimately signed/active, just not meant for this resource.

        `aud` is legal as either a single string or a list of strings per JWT
        conventions, so it's normalized to a list before comparing.
        """
        expected = self.audience or self.resource_uri
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

    def _cache_get(self, token: str) -> Optional[Dict[str, Any]]:
        cached = self._token_cache.get(token)
        if cached is None:
            return None
        result, expires_at = cached
        if time.monotonic() >= expires_at:
            del self._token_cache[token]
            return None
        return result

    def _cache_set(self, token: str, result: Dict[str, Any]) -> None:
        if self.token_cache_seconds <= 0:
            return
        self._token_cache[token] = (result, time.monotonic() + self.token_cache_seconds)

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
        token_introspection_client_secret: Optional[str] = None,
        discovery_port: int = 3005,
        jwks_uri: Optional[str] = None,
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        token_cache_seconds: Optional[int] = None,
        enable_client_registration: Optional[bool] = None,
        static_client_id: Optional[str] = None,
        static_client_secret: Optional[str] = None,
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
