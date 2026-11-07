"""Shared OAuth helpers and types. A leaf module: it imports no other OAuth module."""

import os
from typing import Any, Dict, Optional

from nitrostack.auth.secret import SecretValue

# RFC 7662 introspection result: ``{"active": bool, ...claims}``.
TokenInfo = Dict[str, Any]


def is_oauth_required() -> bool:
    """TS ``OAuthModule.isAuthRequired()`` — Studio/local default is off.

    Set ``OAUTH_REQUIRED=true`` to enforce Bearer tokens. Unset/false lets
    NitroStudio and Inspector call tools against mock Duffel data.
    """
    return (os.environ.get("OAUTH_REQUIRED") or "").strip().lower() == "true"


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


def _has_secret(value: Any) -> bool:
    """True when a secret was given. A SecretValue counts even if it holds no value (it is
    falsy after pickling), so it is never silently replaced by an environment variable;
    unwrapping it raises instead."""
    return isinstance(value, SecretValue) or bool(value)
