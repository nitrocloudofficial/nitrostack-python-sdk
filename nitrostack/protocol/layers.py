"""Runtime layer identifiers matching the Doc 00 architecture diagram."""

from enum import Enum


class RuntimeLayer(str, Enum):
    """Decoupled layers of the NitroStack stateless MCP runtime."""

    TRANSPORT_SECURITY = "transport_security"
    PROTOCOL_DISPATCHER = "protocol_dispatcher"
    REGISTRIES = "registries"
    TASK_MANAGEMENT = "task_management"
