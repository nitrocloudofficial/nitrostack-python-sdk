"""MCP 2026-07-28 implementation blueprint conformance registry."""

from __future__ import annotations

from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Iterable

from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class ConformanceArea(str, Enum):
    """Five verification areas for the MCP 2026-07-28 blueprint."""

    STATELESS_HTTP = "stateless_http"
    JSONRPC = "jsonrpc"
    TASKS = "tasks"
    MULTI_TENANT = "multi_tenant"
    CIMD_SSRF = "cimd_ssrf"


BLUEPRINT_CONFORMANCE_AREAS: dict[ConformanceArea, str] = {
    ConformanceArea.STATELESS_HTTP: (
        "Stateless POST /mcp, server/discover on 2026-07-28, no Mcp-Session-Id, CORS"
    ),
    ConformanceArea.JSONRPC: (
        "Standard JSON-RPC error codes; deprecated methods rejected on modern wire"
    ),
    ConformanceArea.TASKS: (
        "Task-augmented tools/call, tasks/get lifecycle, cancel, terminal-only TTL eviction"
    ),
    ConformanceArea.MULTI_TENANT: (
        "Cross-tenant task access raises TaskNotFoundError without enumeration"
    ),
    ConformanceArea.CIMD_SSRF: (
        "CIMD fetch blocks special-use IPs, redirects, and payloads over 5 KiB"
    ),
}

# Canonical module paths implementing the recommended package layout.
REQUIRED_BLUEPRINT_MODULES: tuple[str, ...] = (
    "nitrostack.core.app",
    "nitrostack.core.context",
    "nitrostack.core.decorators",
    "nitrostack.core.errors",
    "nitrostack.core.task",
    "nitrostack.protocol.version",
    "nitrostack.protocol.schema",
    "nitrostack.protocol.mrtr",
    "nitrostack.protocol.cache_hints",
    "nitrostack.protocol.observability",
    "nitrostack.tasks.store",
    "nitrostack.tasks.memory",
    "nitrostack.auth.cimd",
    "nitrostack.auth.oauth",
    "nitrostack.transports.http",
    "nitrostack.transports.sse",
    "nitrostack.transports.stdio",
)

MODERN_JSONRPC_ERROR_CODES: frozenset[int] = frozenset(
    {
        JsonRpcErrorCode.PARSE_ERROR,
        JsonRpcErrorCode.INVALID_REQUEST,
        JsonRpcErrorCode.METHOD_NOT_FOUND,
        JsonRpcErrorCode.INVALID_PARAMS,
        JsonRpcErrorCode.INTERNAL_ERROR,
    }
)

DEPRECATED_MODERN_METHODS: frozenset[str] = frozenset(
    {
        "tasks/result",
        "tasks/list",
    }
)


def verify_package_layout(modules: Iterable[str] | None = None) -> list[str]:
    """Return import errors for any required blueprint module that cannot load."""
    missing: list[str] = []
    for module_path in modules or REQUIRED_BLUEPRINT_MODULES:
        try:
            import_module(module_path)
        except Exception as exc:  # pragma: no cover - surfaced by tests
            missing.append(f"{module_path}: {exc}")
    return missing


def assert_blueprint_layout() -> None:
    """Raise ``ImportError`` when a required blueprint module is unavailable."""
    errors = verify_package_layout()
    if errors:
        raise ImportError("Blueprint package layout incomplete:\n" + "\n".join(errors))


def protocol_version_matches_blueprint() -> bool:
    return MODERN_PROTOCOL_VERSION == "2026-07-28"
