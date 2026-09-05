"""server/discover capability negotiation (Doc 01 §4)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from nitrostack.protocol.cache_hints import DEFAULT_LIST_CACHE_TTL_MS, build_list_endpoint_cache_hint_meta
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

DISCOVER_RESULT_TYPE = "complete"


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
    """Build the server/discover result payload (Doc 01 §4 / Doc 09 §1)."""
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
