"""One-call setup helpers for JWT, API key and OAuth, plus an environment check.

Each ``setup_*_auth`` helper returns a plain ``dict`` of keyword arguments. Pass it straight
to the matching module::

    JWTModule.for_root(**setup_jwt_auth())
    ApiKeyModule.for_root(**setup_api_key_auth())
    OAuthModule.for_root(**setup_oauth_auth())

Secrets in the returned dict are ``SecretValue`` objects, so printing or logging the config
never shows them.
"""

import os
import secrets
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from nitrostack.auth.secret import SecretValue
from nitrostack.core.errors import ConfigurationError

AUTH_KINDS = ("jwt", "api_key", "oauth")

# Each entry is a group of alternatives: the group is satisfied if ANY name in it is set.
_REQUIRED_ENV: Dict[str, List[Tuple[str, ...]]] = {
    "jwt": [("JWT_SECRET",)],
    "api_key": [("API_KEY",)],
    "oauth": [
        ("RESOURCE_URI",),
        ("AUTH_SERVER_URL",),
        ("JWKS_URI", "OAUTH_INTROSPECTION_ENDPOINT", "INTROSPECTION_ENDPOINT"),
    ],
}

_INSTRUCTIONS = {
    "jwt": """JWT auth setup
  1. Set a long random secret:  JWT_SECRET=<at least 32 random characters>
  2. In your module:           JWTModule.for_root(**setup_jwt_auth())
  Optional: TOKEN_AUDIENCE, TOKEN_ISSUER""",
    "api_key": """API key auth setup
  1. Set one or more keys:     API_KEY=<key>   (or API_KEY_1, API_KEY_2, ...)
  2. In your module:           ApiKeyModule.for_root(**setup_api_key_auth())
  Clients send the key in the x-api-key header.""",
    "oauth": """OAuth auth setup
  1. Set the server identity:  RESOURCE_URI=https://your-server/mcp
                               AUTH_SERVER_URL=https://your-authorization-server
  2. Set ONE way to verify tokens:
       JWKS_URI=https://your-authorization-server/.well-known/jwks.json
       or OAUTH_INTROSPECTION_ENDPOINT=https://your-authorization-server/introspect
  3. In your module:           OAuthModule.for_root(**setup_oauth_auth())
  Optional: TOKEN_AUDIENCE, TOKEN_ISSUER, INTROSPECTION_CLIENT_ID, INTROSPECTION_CLIENT_SECRET""",
}


def _check_kind(kind: str) -> str:
    if kind not in AUTH_KINDS:
        raise ValueError(f"Unknown auth kind {kind!r}; expected one of {', '.join(AUTH_KINDS)}")
    return kind


def _is_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _api_key_names() -> List[str]:
    return [k for k in os.environ if (k == "API_KEY" or k.startswith("API_KEY_")) and _is_set(k)]


def validate_auth_env(kind: str) -> Tuple[bool, List[str]]:
    """Check the environment for ``kind`` (``"jwt"``, ``"api_key"`` or ``"oauth"``).

    Returns ``(valid, missing)``. ``missing`` lists every missing variable, not just the
    first. When any one of several variables would do, they are shown joined with `` or ``.
    """
    _check_kind(kind)
    missing: List[str] = []
    for group in _REQUIRED_ENV[kind]:
        if kind == "api_key":
            satisfied = bool(_api_key_names())
            label = "API_KEY (or API_KEY_<n>)"
        else:
            satisfied = any(_is_set(name) for name in group)
            label = " or ".join(group)
        if not satisfied:
            missing.append(label)
    return (not missing, missing)


def print_auth_setup_instructions(kind: str) -> None:
    """Print how to configure ``kind``. Never prints any secret value."""
    print(_INSTRUCTIONS[_check_kind(kind)])


def _require_env(kind: str) -> None:
    valid, missing = validate_auth_env(kind)
    if not valid:
        raise ConfigurationError(
            f"Missing environment for {kind} auth: {', '.join(missing)}. "
            f"Call print_auth_setup_instructions({kind!r}) for help."
        )


def setup_jwt_auth(
    secret: Union[SecretValue, str, None] = None,
    expires_in: str = "24h",
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    secret_env_var: str = "JWT_SECRET",
) -> Dict[str, Any]:
    """Return keyword arguments for ``JWTModule.for_root``.

    Without ``secret``, the secret is read from ``JWT_SECRET`` and a
    ``ConfigurationError`` is raised if it is not set.
    """
    if secret is None:
        if not _is_set(secret_env_var):
            raise ConfigurationError(
                f"Missing environment for jwt auth: {secret_env_var}. "
                "Call print_auth_setup_instructions('jwt') for help."
            )
        secret = SecretValue.from_env(secret_env_var)
    elif not isinstance(secret, SecretValue):
        secret = SecretValue(secret)
    return {
        "secret_env_var": secret_env_var,
        "expires_in": expires_in,
        "audience": audience or os.environ.get("TOKEN_AUDIENCE") or None,
        "issuer": issuer or os.environ.get("TOKEN_ISSUER") or None,
        "secret": secret,
    }


def setup_api_key_auth(
    keys: Optional[Sequence[Union[SecretValue, str]]] = None,
    header_name: str = "x-api-key",
    hashed: bool = False,
    keys_env_prefix: str = "API_KEY",
) -> Dict[str, Any]:
    """Return keyword arguments for ``ApiKeyModule.for_root``.

    Without ``keys``, at least one ``API_KEY`` / ``API_KEY_<n>`` variable must be set.
    """
    if keys:
        wrapped = [k if isinstance(k, SecretValue) else SecretValue(k) for k in keys]
    else:
        names = [
            k
            for k in os.environ
            if (k == keys_env_prefix or k.startswith(f"{keys_env_prefix}_")) and _is_set(k)
        ]
        if not names:
            raise ConfigurationError(
                f"Missing environment for api_key auth: {keys_env_prefix} "
                f"(or {keys_env_prefix}_<n>). "
                "Call print_auth_setup_instructions('api_key') for help."
            )
        wrapped = []  # the module reads these from the environment itself
    return {
        "keys_env_prefix": keys_env_prefix,
        "header_name": header_name,
        "hashed": hashed,
        "keys": wrapped,
    }


def setup_oauth_auth(
    resource_uri: Optional[str] = None,
    authorization_servers: Optional[Sequence[str]] = None,
    scopes_supported: Optional[Sequence[str]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Return keyword arguments for ``OAuthModule.for_root``.

    Values not passed are read from ``RESOURCE_URI`` and ``AUTH_SERVER_URL``. Token
    verification (``JWKS_URI`` or an introspection endpoint) is picked up by the module
    from the environment, so one of them must be set unless passed in ``overrides``
    (for example ``jwks_uri=...`` or ``token_introspection_endpoint=...``).
    Any other ``OAuthModule.for_root`` argument can be passed as an override.
    """
    resource = resource_uri or os.environ.get("RESOURCE_URI")
    servers = list(authorization_servers or [])
    if not servers and _is_set("AUTH_SERVER_URL"):
        servers = [os.environ["AUTH_SERVER_URL"].strip()]
    has_verifier = (
        overrides.get("jwks_uri")
        or overrides.get("token_introspection_endpoint")
        or any(_is_set(n) for n in _REQUIRED_ENV["oauth"][2])
    )
    missing: List[str] = []
    if not resource:
        missing.append("RESOURCE_URI")
    if not servers:
        missing.append("AUTH_SERVER_URL")
    if not has_verifier:
        missing.append(" or ".join(_REQUIRED_ENV["oauth"][2]))
    if missing:
        raise ConfigurationError(
            f"Missing environment for oauth auth: {', '.join(missing)}. "
            "Call print_auth_setup_instructions('oauth') for help."
        )
    for name in ("token_introspection_client_secret", "static_client_secret"):
        value = overrides.get(name)
        if isinstance(value, str):
            overrides[name] = SecretValue(value)
    return {
        "resource_uri": resource,
        "authorization_servers": servers,
        "scopes_supported": list(scopes_supported or ["read", "write"]),
        **overrides,
    }


def generate_test_credentials() -> Dict[str, SecretValue]:
    """Make random credentials for TESTS AND LOCAL DEMOS ONLY. Never use them in production.

    Fresh on every call. The values are ``SecretValue`` objects, so they are not printed
    by default; call ``.unwrap()`` to read one.
    """
    return {
        "jwt_secret": SecretValue(secrets.token_urlsafe(48)),
        "api_key": SecretValue(f"sk_test_{secrets.token_hex(24)}"),
    }
