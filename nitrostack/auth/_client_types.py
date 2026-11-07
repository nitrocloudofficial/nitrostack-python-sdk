"""Secret-safe OAuth configuration, results, errors, and transport records."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Mapping

from nitrostack.auth.secret import SecretValue


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
    client_secret: str | SecretValue | None = field(default=None, repr=False)
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
               for value in (getattr(self, item.name) for item in fields(self) if item.name != "client_secret")):
            raise OAuthClientError("configuration")
        if self.client_secret is not None and not isinstance(self.client_secret, SecretValue):
            if not isinstance(self.client_secret, str) or not self.client_secret:
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
