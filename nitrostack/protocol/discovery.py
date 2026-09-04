"""server/discover capability negotiation (Doc 01 §4)."""

from __future__ import annotations

from typing import Any, Optional

from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS


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
        "protocolVersion": protocol_version,
        "supportedVersions": versions,
        "serverInfo": {"name": server_name, "version": server_version},
        "capabilities": capabilities,
    }
