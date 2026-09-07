"""Runtime layer identifiers for the MCP 2026-07-28 architecture."""

from enum import Enum


class RuntimeLayer(str, Enum):
    """Decoupled layers of the NitroStack stateless MCP runtime."""

    TRANSPORT_SECURITY = "transport_security"
    PROTOCOL_DISPATCHER = "protocol_dispatcher"
    REGISTRIES = "registries"
    TASK_MANAGEMENT = "task_management"
