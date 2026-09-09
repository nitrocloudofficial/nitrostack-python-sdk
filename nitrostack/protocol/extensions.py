"""Named extension identifiers (SEP-2133) advertised via server/discover."""

from enum import Enum


class MCPExtensionId(str, Enum):
    """Canonical MCP 2.0 extension keys from the capabilities.extensions map."""

    APP = "io.modelcontextprotocol/app"
    TASKS = "io.modelcontextprotocol/tasks"
