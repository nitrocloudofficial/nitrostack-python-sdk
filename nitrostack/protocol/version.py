"""MCP protocol version identifiers and TypeScript-compatible era selection."""

from __future__ import annotations

import os
from typing import Literal, Optional

MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-06-18"

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (MODERN_PROTOCOL_VERSION,)

PROTOCOL_ERA_ENV = "NITRO_MCP_PROTOCOL_VERSION"
ProtocolEra = Literal["modern", "legacy"]

_MODERN_ALIASES = frozenset({"auto", "modern", "latest", MODERN_PROTOCOL_VERSION})
_LEGACY_ALIASES = frozenset({"legacy", LEGACY_PROTOCOL_VERSION})


def resolve_protocol_era(raw: Optional[str] = None) -> Optional[ProtocolEra]:
    """
    Map ``NITRO_MCP_PROTOCOL_VERSION`` the same way as the TypeScript SDK.

    * ``auto`` / ``modern`` / ``latest`` / ``2026-07-28`` → modern (stateless)
    * ``legacy`` / ``2025-06-18`` → legacy (sessionful)
    * unset / empty → ``None`` (keep ``ServerConfig`` defaults)

    Unset does **not** default to ``auto`` here. TypeScript does; Python keeps
    ``stateless=False`` unless the env var (or ``MCP_STATELESS``) is set, so
    existing sessionful apps do not flip silently.
    """
    value = (raw if raw is not None else os.environ.get(PROTOCOL_ERA_ENV) or "").strip().lower()
    if not value:
        return None
    if value in _MODERN_ALIASES:
        return "modern"
    if value in _LEGACY_ALIASES:
        return "legacy"
    return None


def protocol_version_for_era(era: Optional[ProtocolEra], fallback: str = MODERN_PROTOCOL_VERSION) -> str:
    if era == "legacy":
        return LEGACY_PROTOCOL_VERSION
    if era == "modern":
        return MODERN_PROTOCOL_VERSION
    return fallback


def stateless_for_era(era: Optional[ProtocolEra]) -> Optional[bool]:
    """Modern era implies stateless HTTP; legacy implies sessionful. Unset → None."""
    if era == "modern":
        return True
    if era == "legacy":
        return False
    return None
