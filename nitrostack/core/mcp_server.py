"""
Low-level MCP server ownership for nitrostack.

Builds directly on the Python `mcp` SDK's low-level `Server` class: nitrostack
owns registration/dispatch and only declares the protocol-level capabilities
it actually implements.
"""
from typing import Any, Dict, Optional

import mcp.types as types
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.server.lowlevel.server import NotificationOptions


class NitroStackMcpServer(LowLevelServer):
    """
    Thin subclass of the official low-level MCP `Server`.

    One instance owns tool, resource, and prompt registration. The HTTP factory
    mounts that same server on ``/mcp``: sessionless for ``auto`` / ``modern``,
    sessionful for ``legacy``. A second session manager is not created.

    The base `Server.get_capabilities()` only advertises a capability when a
    handler for the corresponding request type has been registered, and it
    always reports `resources.subscribe=False`. This subclass declares:
    `listChanged=True` for tools/resources/prompts, `resources.subscribe=True`
    (nitrostack always registers subscribe/unsubscribe handlers), and the
    `tasks` capability whenever nitrostack's task subsystem handlers are
    registered on this server.
    """

    def __init__(self, name: str, version: Optional[str] = None):
        super().__init__(name=name, version=version)
        self.has_task_support: bool = False
        self.http_engine: Optional[str] = None

    def get_capabilities(
        self,
        notification_options: NotificationOptions,
        experimental_capabilities: Dict[str, Dict[str, Any]],
    ) -> types.ServerCapabilities:
        caps = super().get_capabilities(notification_options, experimental_capabilities)

        if caps.resources is not None:
            caps.resources.subscribe = True

        if self.has_task_support:
            caps.tasks = types.ServerTasksCapability(
                list=types.TasksListCapability(),
                cancel=types.TasksCancelCapability(),
                requests=types.ServerTasksRequestsCapability(
                    tools=types.TasksToolsCapability(call=types.TasksCallCapability())
                ),
            )

        return caps

    def create_initialization_options(self, **kwargs: Any):
        # Match TS's static `listChanged: true` for tools/resources/prompts by
        # default, unless the caller explicitly supplies notification_options.
        kwargs.setdefault(
            "notification_options",
            NotificationOptions(
                prompts_changed=True,
                resources_changed=True,
                tools_changed=True,
            ),
        )
        return super().create_initialization_options(**kwargs)
