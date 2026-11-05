"""Reusable OAuth client, adapted from PR #4 with managed, secret-safe tokens."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import weakref
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Mapping

from nitrostack.auth import cimd
from nitrostack.auth.pkce import generate_pkce_params, is_valid_code_verifier, validate_pkce_support
from nitrostack.auth.token_store import MemoryTokenStore, StoredToken, TokenStore, token_response_to_stored


_OAUTH_ERRORS = frozenset({
    "invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "invalid_scope", "access_denied", "server_error",
    "temporarily_unavailable", "invalid_token", "unsupported_token_type",
    "invalid_redirect_uri", "invalid_client_metadata",
})


class OAuthClientError(ValueError):
    """Only operation, status, and recognized OAuth error codes are exposed."""

    def __init__(self, operation: str, *, status: int | None = None, error_code=None):
        self.operation = operation
        self.status = status
        self.error_code = error_code if isinstance(error_code, str) and error_code in _OAUTH_ERRORS else None
        suffix = f" (HTTP {status})" if status is not None else ""
        if self.error_code:
            suffix += f": {self.error_code}"
        super().__init__(f"OAuth {operation} failed{suffix}")


@dataclass(frozen=True)
class OAuth2ClientConfig:
    client_id: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)
    issuer: str | None = field(default=None, repr=False)
    redirect_uri: str | None = field(default=None, repr=False)
    authorization_endpoint: str | None = field(default=None, repr=False)
    token_endpoint: str | None = field(default=None, repr=False)
    revocation_endpoint: str | None = field(default=None, repr=False)
    registration_endpoint: str | None = field(default=None, repr=False)
    protected_resource_metadata_url: str | None = field(default=None, repr=False)
    authorization_server_metadata_url: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if any(value is not None and (not isinstance(value, str) or not value)
               for value in (getattr(self, item.name) for item in fields(self))):
            raise OAuthClientError("configuration")


@dataclass(frozen=True)
class AuthorizationFlow:
    authorization_url: str = field(repr=False)
    state: str = field(repr=False)
    code_verifier: str = field(repr=False)
    code_challenge: str = field(repr=False)
    code_challenge_method: str = "S256"


@dataclass(frozen=True)
class ClientRegistration:
    client_id: str = field(repr=False)
    client_secret: str | None = field(default=None, repr=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if (not isinstance(self.client_id, str) or not self.client_id
                or (self.client_secret is not None and not isinstance(self.client_secret, str))):
            raise OAuthClientError("registration response")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        """Explicit credential-bearing output; never log this mapping."""
        result = {**self.metadata, "client_id": self.client_id}
        if self.client_secret is not None:
            result["client_secret"] = self.client_secret
        return result


@dataclass(frozen=True)
class _Request:
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(repr=False)
    timeout: float
    pinned_ip: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Response:
    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)


def get_well_known_metadata_uris(resource_url: str) -> list[str]:
    parsed = urllib.parse.urlsplit(resource_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    urls = []
    if parsed.path and parsed.path != "/":
        urls.append(f"{origin}/.well-known/oauth-protected-resource{parsed.path}")
    return list(dict.fromkeys([*urls, f"{origin}/.well-known/oauth-protected-resource"]))


def parse_www_authenticate_header(value: str) -> dict[str, str] | None:
    match = re.search(r"(?:^|,)\s*Bearer\s+", value, re.IGNORECASE)
    if not match:
        return None
    result = {"scheme": "Bearer"}
    for item in re.finditer(r'([\w-]+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^,\s]+))', value[match.end():]):
        text = item.group(2) if item.group(2) is not None else item.group(3)
        result[item.group(1).lower()] = re.sub(r"\\(.)", r"\1", text)
    return result


class OAuth2Client:
    """One configured OAuth client; share it to coalesce refreshes per token key.

    Store keys must distinguish provider, client, principal, and resource. This
    client never launches a browser or listens for authorization callbacks.
    """

    def __init__(
        self, config: OAuth2ClientConfig | Mapping[str, Any], *, store: TokenStore | None = None,
        timeout_seconds: float = 10, max_response_bytes: int = 1024 * 1024,
    ):
        try:
            self.config = config if isinstance(config, OAuth2ClientConfig) else OAuth2ClientConfig(**dict(config))
        except (TypeError, ValueError):
            raise OAuthClientError("configuration") from None
        try:
            valid_timeout = (not isinstance(timeout_seconds, bool) and isinstance(timeout_seconds, (int, float))
                             and math.isfinite(timeout_seconds) and timeout_seconds > 0)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise OAuthClientError("configuration")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise OAuthClientError("configuration")
        self.store = MemoryTokenStore() if store is None else store
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._metadata: dict[str, Any] = {}
        self._refresh_tasks: dict[str, asyncio.Task[StoredToken]] = {}
        self._key_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._trusted_urls = {
            value for item in fields(self.config)
            if item.name.endswith(("endpoint", "metadata_url")) or item.name == "issuer"
            if (value := getattr(self.config, item.name)) is not None
        }
        for url in self._trusted_urls:
            self._validate_url(url)

    def _validate_url(self, url: str) -> None:
        try:
            if not isinstance(url, str) or url != url.strip():
                raise ValueError()
            cimd.validate_fetch_url(url, allow_loopback=url in self._trusted_urls)
            # Trigger port validation before urllib or socket sees malformed input.
            urllib.parse.urlsplit(url).port
        except (TypeError, ValueError):
            raise OAuthClientError("endpoint validation") from None

    def _endpoint(self, name: str) -> str:
        value = getattr(self.config, name) or self._metadata.get(name)
        if not value:
            raise OAuthClientError("endpoint configuration")
        self._validate_url(value)
        return value

    async def _send(self, request: _Request) -> _Response:
        """Internal async transport seam; tests replace it without real network IO."""
        return await asyncio.to_thread(self._send_sync, request)

    def _send_sync(self, request: _Request) -> _Response:
        opener = cimd._build_fetch_opener(request.url, request.pinned_ip)
        outgoing = urllib.request.Request(request.url, data=request.body,
                                          headers=dict(request.headers), method=request.method)
        try:
            response = opener.open(outgoing, timeout=request.timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            length = response.headers.get("Content-Length")
            if length is not None and not 0 <= int(length) <= self.max_response_bytes:
                raise OAuthClientError("response size")
            body = response.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise OAuthClientError("response size")
            return _Response(response.code, dict(response.headers), body)

    async def _request(
        self, operation: str, method: str, url: str, *, body: bytes | None = None,
        headers: Mapping[str, str] | None = None, allowed_statuses: tuple[int, ...] = (),
    ) -> _Response:
        try:
            self._validate_url(url)
            pinned_ip = None
            if url not in self._trusted_urls:
                pinned_ip = await cimd.assert_safe_fetch_target(url, require_non_root_path=False)
                if not pinned_ip:
                    raise OAuthClientError("endpoint validation")
            response = await self._send(_Request(
                method, url, {"Accept": "application/json", **(headers or {})}, body,
                self.timeout_seconds, pinned_ip,
            ))
            if len(response.body) > self.max_response_bytes:
                raise OAuthClientError("response size")
        except OAuthClientError:
            raise
        except Exception:
            raise OAuthClientError(operation) from None
        if not (200 <= response.status < 300 or response.status in allowed_statuses):
            error_code = None
            try:
                error_code = json.loads(response.body).get("error")
            except (ValueError, AttributeError, TypeError):
                pass
            raise OAuthClientError(operation, status=response.status, error_code=error_code)
        return response

    @staticmethod
    def _json(response: _Response, operation: str) -> dict[str, Any]:
        try:
            result = json.loads(response.body)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise OAuthClientError(operation) from None

    async def discover_protected_resource_metadata(
        self, resource_url: str, *, www_authenticate_header: str | None = None,
    ) -> dict[str, Any]:
        self._validate_url(resource_url)
        configured = self.config.protected_resource_metadata_url
        if configured:
            candidates = [configured]
        else:
            if www_authenticate_header is None:
                try:
                    response = await self._request("resource discovery", "GET", resource_url,
                                                   allowed_statuses=(401,))
                    www_authenticate_header = next((value for key, value in response.headers.items()
                                                   if key.lower() == "www-authenticate"), "")
                except OAuthClientError:
                    www_authenticate_header = ""
            challenge = parse_www_authenticate_header(www_authenticate_header or "") or {}
            candidates = list(dict.fromkeys([
                *([challenge["resource_metadata"]] if challenge.get("resource_metadata") else []),
                *get_well_known_metadata_uris(resource_url),
            ]))
        for url in candidates:
            try:
                metadata = self._json(await self._request("resource discovery", "GET", url), "resource metadata")
                if metadata.get("resource") != resource_url:
                    raise OAuthClientError("resource metadata")
                servers = metadata.get("authorization_servers")
                if not isinstance(servers, list) or not servers:
                    raise OAuthClientError("resource metadata")
                for issuer in servers:
                    self._validate_url(issuer)
                return metadata
            except OAuthClientError:
                continue
        raise OAuthClientError("resource discovery")

    async def discover_authorization_server_metadata(self, issuer: str | None = None) -> dict[str, Any]:
        issuer = issuer or self.config.issuer
        self._validate_url(issuer)
        parsed = urllib.parse.urlsplit(issuer)
        if parsed.query:
            raise OAuthClientError("issuer validation")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        configured = self.config.authorization_server_metadata_url
        candidates = [configured] if configured else list(dict.fromkeys([
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        ]))
        for url in candidates:
            try:
                metadata = self._json(await self._request("authorization discovery", "GET", url), "authorization metadata")
                if metadata.get("issuer") != issuer or not metadata.get("token_endpoint"):
                    raise OAuthClientError("authorization metadata")
                for name in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
                    if name in metadata:
                        self._validate_url(metadata[name])
                self._metadata = dict(metadata)
                return dict(metadata)
            except OAuthClientError:
                continue
        raise OAuthClientError("authorization discovery")

    def _client_id(self) -> str:
        if not self.config.client_id:
            raise OAuthClientError("client configuration")
        return self.config.client_id

    def _key_lock(self, key: str) -> asyncio.Lock:
        if not isinstance(key, str) or not key:
            raise OAuthClientError("token key")
        lock = self._key_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._key_locks[key] = lock
        return lock

    async def start_authorization_flow(
        self, *, scope: str | None = None, state: str | None = None,
        resource: str | None = None, redirect_uri: str | None = None,
    ) -> AuthorizationFlow:
        redirect_uri = redirect_uri or self.config.redirect_uri
        if not redirect_uri or (state is not None and (not isinstance(state, str) or not state)):
            raise OAuthClientError("authorization configuration")
        if self._metadata and not validate_pkce_support(self._metadata.get("code_challenge_methods_supported")):
            raise OAuthClientError("PKCE support")
        pkce = generate_pkce_params("S256")
        state = secrets.token_urlsafe(32) if state is None else state
        params = {"response_type": "code", "client_id": self._client_id(), "redirect_uri": redirect_uri,
                  "state": state, "code_challenge": pkce["code_challenge"], "code_challenge_method": "S256"}
        if scope is not None:
            params["scope"] = scope
        if resource is not None:
            params["resource"] = resource
        endpoint = urllib.parse.urlsplit(self._endpoint("authorization_endpoint"))
        reserved = {"response_type", "client_id", "redirect_uri", "state", "code_challenge",
                    "code_challenge_method", "scope", "resource"}
        query = [(key, value) for key, value in urllib.parse.parse_qsl(endpoint.query, keep_blank_values=True)
                 if key not in reserved]
        authorization_url = urllib.parse.urlunsplit(endpoint._replace(query=urllib.parse.urlencode([*query, *params.items()])))
        return AuthorizationFlow(authorization_url, state, pkce["code_verifier"], pkce["code_challenge"])

    def _authenticate(self, params: dict[str, Any]) -> dict[str, str]:
        params["client_id"] = self._client_id()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.config.client_secret is not None:
            credentials = ":".join(urllib.parse.quote_plus(value, safe="") for value in
                                   (self._client_id(), self.config.client_secret))
            headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode("ascii")).decode("ascii")
        return headers

    async def _obtain_token(self, key, params, *, resource=None, previous=None):
        if not isinstance(key, str) or not key:
            raise OAuthClientError("token key")
        headers = self._authenticate(params)
        if resource is not None:
            params["resource"] = resource
        response = await self._request("token request", "POST", self._endpoint("token_endpoint"),
                                       body=urllib.parse.urlencode(params).encode("utf-8"), headers=headers)
        try:
            token = token_response_to_stored(self._json(response, "token response"), resource, previous=previous)
            await self.store.save_token(key, token)
        except Exception:
            raise OAuthClientError("token persistence or response") from None
        return token

    async def exchange_code_for_token(
        self, key: str, *, code: str, code_verifier: str,
        redirect_uri: str | None = None, resource: str | None = None,
    ) -> StoredToken:
        redirect_uri = redirect_uri or self.config.redirect_uri
        if (not isinstance(code, str) or not code or not isinstance(code_verifier, str)
                or not is_valid_code_verifier(code_verifier) or not redirect_uri):
            raise OAuthClientError("code exchange configuration")
        async with self._key_lock(key):
            return await self._obtain_token(key, {"grant_type": "authorization_code", "code": code,
                                                "code_verifier": code_verifier, "redirect_uri": redirect_uri}, resource=resource)

    async def get_client_credentials_token(
        self, key: str, *, scope: str | None = None, resource: str | None = None,
    ) -> StoredToken:
        if not self.config.client_secret:
            raise OAuthClientError("client credentials configuration")
        params = {"grant_type": "client_credentials"}
        if scope is not None:
            params["scope"] = scope
        async with self._key_lock(key):
            return await self._obtain_token(key, params, resource=resource)

    async def _refresh(self, key: str) -> StoredToken:
        async with self._key_lock(key):
            return await self._refresh_locked(key)

    async def _refresh_locked(self, key: str) -> StoredToken:
        try:
            previous = await self.store.get_token(key)
        except Exception:
            raise OAuthClientError("token retrieval") from None
        if previous is None or not previous.refresh_token:
            raise OAuthClientError("refresh credential")
        params = {"grant_type": "refresh_token", "refresh_token": previous.refresh_token}
        if previous.scope is not None:
            params["scope"] = previous.scope
        return await self._obtain_token(key, params, resource=previous.resource, previous=previous)

    async def refresh_token(self, key: str) -> StoredToken:
        if not isinstance(key, str) or not key:
            raise OAuthClientError("token key")
        task = self._refresh_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._refresh(key))
            self._refresh_tasks[key] = task

            def completed(done):
                if self._refresh_tasks.get(key) is done:
                    self._refresh_tasks.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Consume failures even if every waiter cancelled.

            task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def revoke_token(self, key: str, *, token_type_hint: str | None = None) -> None:
        async with self._key_lock(key):
            await self._revoke_locked(key, token_type_hint)

    async def _revoke_locked(self, key: str, token_type_hint: str | None) -> None:
        try:
            stored = await self.store.get_token(key)
            if stored is None:
                return
            hint = token_type_hint or ("refresh_token" if stored.refresh_token else "access_token")
            if hint not in ("access_token", "refresh_token") or not getattr(stored, hint):
                raise OAuthClientError("revocation configuration")
            params = {"token": getattr(stored, hint), "token_type_hint": hint}
            headers = self._authenticate(params)
            await self._request("revocation", "POST", self._endpoint("revocation_endpoint"),
                                body=urllib.parse.urlencode(params).encode("utf-8"), headers=headers)
            await self.store.delete_token(key)
        except OAuthClientError:
            raise
        except Exception:
            raise OAuthClientError("revocation persistence") from None

    async def register_client(self, metadata: Mapping[str, Any]) -> ClientRegistration:
        try:
            body = json.dumps(dict(metadata)).encode("utf-8")
        except Exception:
            raise OAuthClientError("registration configuration") from None
        response = await self._request("registration", "POST", self._endpoint("registration_endpoint"),
                                       body=body, headers={"Content-Type": "application/json"})
        result = self._json(response, "registration response")
        try:
            return ClientRegistration(result["client_id"], result.get("client_secret"), result)
        except (KeyError, TypeError, ValueError):
            raise OAuthClientError("registration response") from None
