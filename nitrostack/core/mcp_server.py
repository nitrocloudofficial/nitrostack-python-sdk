"""
Low-level MCP server ownership for nitrostack.

Builds on official ``mcp`` 2.x ``Server``: nitrostack owns registration and
declares only the capabilities it implements. ``/mcp`` is the v2
``streamable_http_app()`` for every era.
"""
from typing import Any, Callable, Dict, Optional

import mcp.types as types
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler


class NitroStackMcpServer(LowLevelServer):
    """
    Thin subclass of the official low-level MCP ``Server``.

    One instance owns tool, resource, and prompt registration. The HTTP factory
    mounts that same server on ``/mcp`` via ``streamable_http_app()``.
    Sessionful 1.x is not used; v2 serves both protocol eras.
    """

    def __init__(self, name: str, version: Optional[str] = None):
        self.subscription_bus = InMemorySubscriptionBus()
        self.listen_handler = ListenHandler(self.subscription_bus)
        super().__init__(
            name=name,
            version=version or "",
            on_subscriptions_listen=self.listen_handler,
        )
        self.has_task_support: bool = False
        self.http_engine: Optional[str] = None
        self.sessionful: bool = False
        self.discover_handler: Optional[Callable[[], dict[str, Any]]] = None
        self.initialize_handler: Optional[Callable[[Optional[str]], dict[str, Any]]] = None
        # In-process tests still look up handlers by request type.
        self.request_handlers: Dict[Any, Callable[..., Any]] = {}
        self.notification_handlers: Dict[Any, Callable[..., Any]] = {}

    def handle_server_discover(self) -> dict[str, Any]:
        """Answer ``server/discover`` from this server instance."""
        if self.discover_handler is None:
            raise RuntimeError("server/discover is not configured on this server")
        return self.discover_handler()

    def handle_sessionless_initialize(self, requested_version: Optional[str] = None) -> dict[str, Any]:
        """Answer sessionless ``initialize`` from this server instance."""
        if self.initialize_handler is None:
            raise RuntimeError("sessionless initialize is not configured on this server")
        return self.initialize_handler(requested_version)

    def get_capabilities(
        self,
        notification_options: Optional[NotificationOptions] = None,
        experimental_capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
        extensions: Optional[Dict[str, Dict[str, Any]]] = None,
        *,
        protocol_version: Optional[str] = None,
    ) -> types.ServerCapabilities:
        caps = super().get_capabilities(
            notification_options,
            experimental_capabilities,
            extensions,
            protocol_version=protocol_version,
        )

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
        kwargs.setdefault(
            "notification_options",
            NotificationOptions(
                prompts_changed=True,
                resources_changed=True,
                tools_changed=True,
            ),
        )
        return super().create_initialization_options(**kwargs)
