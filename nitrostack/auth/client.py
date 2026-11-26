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
from dataclasses import fields
from typing import Any, Mapping

from nitrostack.auth import cimd
# Re-export the original import locations for callers and transport test seams.
from nitrostack.auth._client_types import (
    AuthorizationFlow, ClientRegistration, OAuth2ClientConfig, OAuthClientError,
    _Request, _Response,
)
from nitrostack.auth.secret import unwrap_secret
from nitrostack.auth.pkce import generate_pkce_params, is_valid_code_verifier, validate_pkce_support
from nitrostack.auth.token_store import (
    MemoryTokenStore, StoredToken, TokenStore, is_token_expired, token_response_to_stored,
)


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
        self._force_refresh: dict[str, bool] = {}
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
            try:
                secret = unwrap_secret(self.config.client_secret)
                if not secret:
                    raise ValueError
            except Exception:
                raise OAuthClientError("client authentication") from None
            credentials = ":".join(urllib.parse.quote_plus(value, safe="") for value in
                                   (self._client_id(), secret))
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

    async def _refresh(self, key: str, *, only_if_expired: bool = False) -> StoredToken:
        async with self._key_lock(key):
            return await self._refresh_locked(key, only_if_expired=only_if_expired)

    async def _refresh_locked(self, key: str, *, only_if_expired: bool = False) -> StoredToken:
        try:
            previous = await self.store.get_token(key)
        except Exception:
            raise OAuthClientError("token retrieval") from None
        if (only_if_expired and not self._force_refresh.get(key, False)
                and previous is not None and not is_token_expired(previous)):
            return previous
        if previous is None or not previous.refresh_token:
            raise OAuthClientError("refresh credential")
        params = {"grant_type": "refresh_token", "refresh_token": previous.refresh_token}
        if previous.scope is not None:
            params["scope"] = previous.scope
        return await self._obtain_token(key, params, resource=previous.resource, previous=previous)

    async def get_access_token(self, key: str) -> str:
        """Return a usable access token, refreshing and persisting when necessary."""
        token = await self._coalesced_refresh(key, only_if_expired=True)
        if is_token_expired(token):
            raise OAuthClientError("access token expiry")
        return token.access_token

    async def refresh_token(self, key: str) -> StoredToken:
        return await self._coalesced_refresh(key)

    async def _coalesced_refresh(self, key: str, *, only_if_expired: bool = False) -> StoredToken:
        if not isinstance(key, str) or not key:
            raise OAuthClientError("token key")
        task = self._refresh_tasks.get(key)
        if task is None or task.done():
            self._force_refresh[key] = not only_if_expired
            task = asyncio.create_task(self._refresh(key, only_if_expired=only_if_expired))
            self._refresh_tasks[key] = task

            def completed(done):
                if self._refresh_tasks.get(key) is done:
                    self._refresh_tasks.pop(key, None)
                    self._force_refresh.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Consume failures even if every waiter cancelled.

            task.add_done_callback(completed)
        elif not only_if_expired:
            self._force_refresh[key] = True
        return await asyncio.shield(task)

    async def revoke_token(
        self, key: str, *, token_type_hint: str | None = None, local_fallback: bool = False,
    ) -> None:
        """Revoke remotely; opt in to local deletion if remote revocation fails."""
        if type(local_fallback) is not bool:
            raise OAuthClientError("revocation configuration")
        async with self._key_lock(key):
            await self._revoke_locked(key, token_type_hint, local_fallback)

    async def _revoke_locked(self, key: str, token_type_hint: str | None, local_fallback: bool) -> None:
        try:
            stored = await self.store.get_token(key)
            if stored is None:
                return
            hint = token_type_hint if token_type_hint is not None else (
                "refresh_token" if stored.refresh_token else "access_token"
            )
            if hint not in ("access_token", "refresh_token") or not getattr(stored, hint):
                raise OAuthClientError("revocation configuration")
            params = {"token": getattr(stored, hint), "token_type_hint": hint}
            try:
                headers = self._authenticate(params)
                await self._request("revocation", "POST", self._endpoint("revocation_endpoint"),
                                    body=urllib.parse.urlencode(params).encode("utf-8"), headers=headers)
            except OAuthClientError:
                if not local_fallback:
                    raise
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
