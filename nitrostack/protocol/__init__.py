"""MCP 2.0 protocol layer — version constants, extensions, and wire invariants."""

from nitrostack.protocol.constants import (
    LEGACY_SESSION_HEADER,
    MAX_CIMD_BYTES,
    MAX_SCHEMA_DEPTH,
)
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.layers import RuntimeLayer
from nitrostack.protocol.version import (
    LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.jsonrpc import parse_jsonrpc_request, build_ping_response

__all__ = [
    "LEGACY_PROTOCOL_VERSION",
    "MODERN_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "MCPExtensionId",
    "RuntimeLayer",
    "LEGACY_SESSION_HEADER",
    "MAX_CIMD_BYTES",
    "MAX_SCHEMA_DEPTH",
    "build_discover_result",
    "parse_jsonrpc_request",
    "build_ping_response",
]
