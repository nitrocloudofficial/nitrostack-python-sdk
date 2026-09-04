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

__all__ = [
    "LEGACY_PROTOCOL_VERSION",
    "MODERN_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "MCPExtensionId",
    "RuntimeLayer",
    "LEGACY_SESSION_HEADER",
    "MAX_CIMD_BYTES",
    "MAX_SCHEMA_DEPTH",
]
