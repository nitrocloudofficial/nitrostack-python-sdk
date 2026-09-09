"""server/discover capability negotiation.

Single payload builder for the HTTP engine. The ingress pipeline must not
construct a second discover result.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from nitrostack.protocol.cache_hints import DEFAULT_LIST_CACHE_TTL_MS, build_list_endpoint_cache_hint_meta
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.version import (
    LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)

SERVER_DISCOVER_METHOD = "server/discover"
DISCOVER_RESULT_TYPE = "complete"
INITIALIZE_METHOD = "initialize"
INITIALIZED_NOTIFICATION = "notifications/initialized"
_SESSIONLESS_INITIALIZE_VERSIONS = frozenset(
    {LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION, "2025-11-25"}
)


def build_discover_result(
    *,
    server_name: str,
    server_version: str,
    protocol_version: str = MODERN_PROTOCOL_VERSION,
    supported_versions: Optional[tuple[str, ...]] = None,
    advertise_tasks: bool = True,
    advertise_app: bool = False,
    custom_extensions: Optional[dict[str, str]] = None,
    tools_list_changed: bool = True,
    resources_list_changed: bool = True,
    prompts_list_changed: bool = True,
    ttl_ms: int = DEFAULT_LIST_CACHE_TTL_MS,
    cache_scope: Literal["public", "private"] = "private",
) -> dict[str, Any]:
    """Build the ``server/discover`` result for the mounted HTTP engine."""
    versions = list(supported_versions or SUPPORTED_PROTOCOL_VERSIONS)
    extensions: dict[str, dict[str, str]] = {}
    if advertise_app:
        extensions[MCPExtensionId.APP.value] = {"version": protocol_version}
    if advertise_tasks:
        extensions[MCPExtensionId.TASKS.value] = {"version": protocol_version}
    for extension_id, version in (custom_extensions or {}).items():
        if extension_id and version:
            extensions[str(extension_id)] = {"version": str(version)}

    capabilities: dict[str, Any] = {
        "tools": {"listChanged": tools_list_changed},
        "resources": {"subscribe": False, "listChanged": resources_list_changed},
        "prompts": {"listChanged": prompts_list_changed},
    }
    if extensions:
        capabilities["extensions"] = extensions

    return {
        "resultType": DISCOVER_RESULT_TYPE,
        "protocolVersion": protocol_version,
        "supportedVersions": versions,
        "serverInfo": {"name": server_name, "version": server_version},
        "capabilities": capabilities,
        "ttlMs": ttl_ms,
        "cacheScope": cache_scope,
        "_meta": build_list_endpoint_cache_hint_meta(ttl_ms=ttl_ms, cache_scope=cache_scope),
    }


def build_sessionless_initialize_result(
    *,
    server_name: str,
    server_version: str,
    requested_version: Optional[str] = None,
    protocol_version: str = MODERN_PROTOCOL_VERSION,
    advertise_tasks: bool = True,
    advertise_app: bool = False,
    custom_extensions: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """
    Initialize-shaped result for era ``auto`` with no session.

    Official v2 ``legacy: 'stateless'`` is not mounted yet. This adapter
    answers 2025 ``initialize`` on the same engine as ``server/discover``.
    """
    requested = (requested_version or "").strip()
    negotiated = requested if requested in _SESSIONLESS_INITIALIZE_VERSIONS else protocol_version
    discovered = build_discover_result(
        server_name=server_name,
        server_version=server_version,
        protocol_version=protocol_version,
        advertise_tasks=advertise_tasks,
        advertise_app=advertise_app,
        custom_extensions=custom_extensions,
    )
    return {
        "protocolVersion": negotiated,
        "capabilities": discovered["capabilities"],
        "serverInfo": discovered["serverInfo"],
    }
