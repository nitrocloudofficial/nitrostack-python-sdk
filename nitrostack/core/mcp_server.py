"""
Low-level MCP server ownership for nitrostack.

Builds on official ``mcp`` 2.x ``Server``: nitrostack owns registration and
declares only the capabilities it implements. ``/mcp`` is the v2
``streamable_http_app()`` for every era.
"""
from typing import Any, Callable, Dict, Optional

import mcp.types as types
from mcp import MCPError
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from nitrostack.protocol.errors import JsonRpcErrorCode
from nitrostack.protocol.version import (
    MODERN_PROTOCOL_VERSION,
    ProtocolEra,
    supports_resource_subscriptions,
    supported_protocol_versions_for_era,
)
from nitrostack.protocol.meta import flatten_request_meta_object
from nitrostack.protocol.jsonrpc import HeaderBodyMismatchError, validate_protocol_version_header_meta


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
        self.protocol_era: Optional[ProtocolEra] = None
        self.middleware.append(self._enforce_protocol_era)
        self.discover_handler: Optional[Callable[[], dict[str, Any]]] = None
        self.initialize_handler: Optional[Callable[[Optional[str]], dict[str, Any]]] = None
        # In-process tests still look up handlers by request type.
        self.request_handlers: Dict[Any, Callable[..., Any]] = {}
        self.notification_handlers: Dict[Any, Callable[..., Any]] = {}

    async def _enforce_protocol_era(self, context, call_next):
        """Apply era policy inside the official runner, after its JSON-RPC parsing."""
        if self.protocol_era == "modern" and context.method in (
            "initialize", "notifications/initialized",
        ):
            raise MCPError(types.METHOD_NOT_FOUND, f"Method not found: {context.method}")
        request_headers = getattr(getattr(context, "request", None), "headers", {})
        meta = flatten_request_meta_object(context.meta)
        header_version = request_headers.get("mcp-protocol-version")
        meta_version = meta.get("io.modelcontextprotocol/protocolVersion")
        try:
            validate_protocol_version_header_meta(header_version, meta_version)
        except HeaderBodyMismatchError as exc:
            raise MCPError(int(exc.code), exc.message) from None
        claimed_version = header_version or meta_version
        # The official SDK uses a 2025-03-26 default when a legacy-compatible
        # HTTP request omits the header. Do not reject that implicit fallback.
        unsupported_claim = self.protocol_era is not None and claimed_version and (
            claimed_version not in supported_protocol_versions_for_era(self.protocol_era)
        )
        modern_legacy_request = (
            self.protocol_era == "modern" and context.protocol_version != MODERN_PROTOCOL_VERSION
        )
        if unsupported_claim or modern_legacy_request:
            raise MCPError(int(JsonRpcErrorCode.UNSUPPORTED_PROTOCOL_VERSION), "Unsupported protocol version")
        return await call_next(context)

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
            caps.resources.subscribe = supports_resource_subscriptions(self.protocol_era)

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
