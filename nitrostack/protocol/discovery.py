"""server/discover capability negotiation.

Single payload builder for the HTTP engine. The ingress pipeline must not
construct a second discover result.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from nitrostack.protocol.cache_hints import DEFAULT_LIST_CACHE_TTL_MS, build_list_endpoint_cache_hint_meta
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

SERVER_DISCOVER_METHOD = "server/discover"
DISCOVER_RESULT_TYPE = "complete"


def build_discover_result(
    *,
    server_name: str,
    server_version: str,
    protocol_version: str = MODERN_PROTOCOL_VERSION,
    supported_versions: Optional[tuple[str, ...]] = None,
    advertise_tasks: bool = False,
    advertise_app: bool = False,
    custom_extensions: Optional[dict[str, str]] = None,
    tools_list_changed: bool = True,
    resources_list_changed: bool = True,
    resources_subscribe: bool = False,
    prompts_list_changed: bool = True,
    ttl_ms: int = DEFAULT_LIST_CACHE_TTL_MS,
    cache_scope: Literal["public", "private"] = "private",
) -> dict[str, Any]:
    """Build the ``server/discover`` result for the mounted HTTP engine.

    ``resources_subscribe`` must match ``NitroStackMcpServer.get_capabilities``
    for the same era, so ``server/discover`` and real capability negotiation
    never disagree on ``resources.subscribe``.
    """
    versions = list(supported_versions or SUPPORTED_PROTOCOL_VERSIONS)
    extensions: dict[str, dict[str, str]] = {}
    if advertise_app:
        extensions[MCPExtensionId.APP.value] = {"version": protocol_version}
    if advertise_tasks:
        extensions[MCPExtensionId.TASKS.value] = {"version": protocol_version}
    for extension_id, version in (custom_extensions or {}).items():
        if extension_id and version and (extension_id != MCPExtensionId.TASKS.value or advertise_tasks):
            extensions[str(extension_id)] = {"version": str(version)}

    capabilities: dict[str, Any] = {
        "tools": {"listChanged": tools_list_changed},
        "resources": {"subscribe": resources_subscribe, "listChanged": resources_list_changed},
        "prompts": {"listChanged": prompts_list_changed},
    }
    if extensions:
        capabilities["extensions"] = extensions

    server_info = {"name": server_name, "version": server_version}
    # 2026-07-28 requires `serverInfo` on the result `_meta`; the top-level
    # copy stays for `auto`/legacy clients that read it from the body.
    meta = build_list_endpoint_cache_hint_meta(ttl_ms=ttl_ms, cache_scope=cache_scope)
    meta["serverInfo"] = server_info

    return {
        "resultType": DISCOVER_RESULT_TYPE,
        "protocolVersion": protocol_version,
        "supportedVersions": versions,
        "serverInfo": server_info,
        "capabilities": capabilities,
        "ttlMs": ttl_ms,
        "cacheScope": cache_scope,
        "_meta": meta,
    }
