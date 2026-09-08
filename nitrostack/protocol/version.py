"""MCP protocol version identifiers and protocol-era selection."""

from __future__ import annotations

import os
from typing import Literal, Optional

MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-06-18"

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (MODERN_PROTOCOL_VERSION,)

PROTOCOL_ERA_ENV = "NITRO_MCP_PROTOCOL_VERSION"
STATELESS_OVERRIDE_ENV = "MCP_STATELESS"

ProtocolEra = Literal["legacy", "modern", "auto"]
# How the HTTP factory should treat 2025-shaped traffic for this era.
# ``stateless`` here is the dual-spec fallback (sessionless initialize), not
# the 1.x ``StreamableHTTPSessionManager(stateless=True)`` flag.
WireMode = Literal["sessionful", "stateless", "reject"]
# Which /mcp HTTP engine the era factory mounts. Official mcp 2.x replaces
# the sessionless 1.x manager when that dependency is installed.
HttpEngine = Literal["sessionless", "sessionful"]

_AUTO_ALIASES = frozenset({"auto", "both", "dual", "dual-spec"})
_MODERN_ALIASES = frozenset({"modern", "latest", "2026", MODERN_PROTOCOL_VERSION})
_LEGACY_ALIASES = frozenset({"legacy", "2025", "2025-11-25", LEGACY_PROTOCOL_VERSION})
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def _parse_bool_token(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value in _TRUE_TOKENS:
        return True
    if value in _FALSE_TOKENS:
        return False
    return None


def _era_token(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def resolve_protocol_era(
    raw: Optional[str] = None,
    *,
    stateless_override: Optional[str] = None,
    config_value: Optional[str] = None,
) -> ProtocolEra:
    """
    Resolve the active protocol era.

    Precedence:
    1. ``MCP_STATELESS`` (explicit boolean)
    2. ``NITRO_MCP_PROTOCOL_VERSION`` (or the ``raw`` argument)
    3. ``ServerConfig.protocol_era`` (``config_value``)
    4. ``auto``

    Tokens (case-insensitive, trimmed): ``modern`` / ``latest`` / ``2026`` /
    ``2026-07-28``; ``auto`` / ``both`` / ``dual`` / ``dual-spec``; ``legacy`` /
    ``2025`` / ``2025-06-18`` / ``2025-11-25``. Unknown tokens resolve to
    ``auto``.

    ``auto`` is not ``modern``. ``modern`` is stateless-only; ``auto`` is the
    dual-spec era and does not force the 1.x ``stateless=True`` transport flag.
    """
    override = (
        stateless_override
        if stateless_override is not None
        else os.environ.get(STATELESS_OVERRIDE_ENV)
    )
    flag = _parse_bool_token(override)
    if flag is True:
        return "modern"
    if flag is False:
        return "legacy"

    if raw is not None:
        value = _era_token(raw)
    else:
        value = _era_token(os.environ.get(PROTOCOL_ERA_ENV))
        if not value:
            value = _era_token(config_value)
    if not value or value in _AUTO_ALIASES:
        return "auto"
    if value in _MODERN_ALIASES:
        return "modern"
    if value in _LEGACY_ALIASES:
        return "legacy"
    return "auto"


def supported_protocol_versions_for_era(era: ProtocolEra) -> frozenset[str]:
    """Dated protocol versions this era accepts on the wire header or envelope."""
    if era == "modern":
        return frozenset({MODERN_PROTOCOL_VERSION})
    if era == "legacy":
        return frozenset({LEGACY_PROTOCOL_VERSION})
    return frozenset({MODERN_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION})


def protocol_era_for_wire_mode(wire_mode: WireMode) -> ProtocolEra:
    """Map dual-spec wire mode onto the era that owns its supported versions."""
    if wire_mode == "reject":
        return "modern"
    if wire_mode == "sessionful":
        return "legacy"
    return "auto"


def protocol_version_for_era(era: Optional[ProtocolEra], fallback: str = MODERN_PROTOCOL_VERSION) -> str:
    if era == "legacy":
        return LEGACY_PROTOCOL_VERSION
    if era in ("modern", "auto"):
        return MODERN_PROTOCOL_VERSION
    return fallback


def stateless_for_era(era: Optional[ProtocolEra]) -> Optional[bool]:
    """
    Map era onto the 1.x Streamable HTTP ``stateless`` flag.

    ``modern`` → True, ``legacy`` → False, ``auto`` → None so the HTTP factory
    does not treat dual-spec as modern-only.
    """
    if era == "modern":
        return True
    if era == "legacy":
        return False
    return None


def wire_mode_for_era(era: ProtocolEra) -> WireMode:
    """
    Dual-spec policy for an era.

    * ``legacy`` — sessionful 2025 wire only
    * ``auto`` — accept 2025 ``initialize`` without a session (official v2 fallback)
    * ``modern`` — reject 2025 sessionful wire
    """
    if era == "modern":
        return "reject"
    if era == "auto":
        return "stateless"
    return "sessionful"


def needs_modern_engine(era: ProtocolEra) -> bool:
    """True when ``/mcp`` should be the official 2026 engine (``modern`` or ``auto``)."""
    return era in ("modern", "auto")


def needs_sessionful_engine(era: ProtocolEra) -> bool:
    """True only for ``legacy``. ``auto`` does not mount a second session manager."""
    return era == "legacy"


def http_engine_for_era(era: ProtocolEra) -> HttpEngine:
    """
    Select the /mcp HTTP engine for an era.

    ``legacy`` uses the sessionful 1.x manager. ``modern`` and ``auto`` use the
    sessionless /mcp path (one engine; official mcp 2.x when mounted).
    """
    if needs_sessionful_engine(era):
        return "sessionful"
    return "sessionless"
