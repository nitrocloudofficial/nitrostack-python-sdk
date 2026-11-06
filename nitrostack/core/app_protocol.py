"""Protocol protocol handlers implementation mixin."""

import logging
from typing import Any, Dict, Optional

import mcp.types as types
from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    GetPromptRequestParams,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    RequestParams,
    SubscribeRequestParams,
    UnsubscribeRequestParams,
)

from nitrostack.core.app_context import (
    _apply_request_envelope,
    _auth_metadata_from_request_ctx,
    _bind_correlation,
    _read_contents_to_result,
    _trace_context_from_request_ctx,
)
from nitrostack.core.context import ExecutionContext
from nitrostack.core.errors import (
    ResourceNotFoundError,
)
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.core.visibility import VisibilityTransform
from nitrostack.protocol.cache_hints import (
    build_list_endpoint_cache_hint_meta,
)
from nitrostack.protocol.deprecated import (
    deprecated_method_message,
)
from nitrostack.protocol.version import (
    protocol_version_for_era,
    rejects_legacy_initialize,
    supports_resource_subscriptions,
)
from nitrostack.runtime.caller import caller_identity
from nitrostack.runtime.request_ctx import ServerResult, request_ctx
from nitrostack.transforms import (
    TransformContext,
    TransformError,
)

logger = logging.getLogger(__name__)


class ProtocolHandlersMixin:
    def _tasks_visible_on_wire(self) -> bool:
        # Tasks remain available to in-process callers and the legacy wire only.
        # MCP 2026 has no complete tasks surface (notably tasks/update), so
        # nothing on the 2026 wire may advertise this partial implementation.
        return self.protocol_era not in ("auto", "modern")

    def _advertise_tasks_extension(self) -> bool:
        # Legacy task handlers do not implement the complete 2026 extension.
        # Advertising it is deferred to the dedicated tasks issue.
        return False

    def _custom_extensions(self) -> Optional[Dict[str, str]]:
        extensions = getattr(self.server_config, "extensions", None)
        if not extensions:
            return None
        return dict(extensions)

    def handle_server_discover(self, protocol_version: Optional[str] = None) -> Dict[str, Any]:
        """Single ``server/discover`` result for the mounted HTTP engine."""
        from nitrostack.protocol.discovery import build_discover_result

        has_widgets = any(
            getattr(entry, "component", None) is not None
            for entry in getattr(self, "_tools", {}).values()
        )
        version = protocol_version or protocol_version_for_era(
            getattr(self, "protocol_era", None),
            self.server_config.protocol_version,
        )
        # Same source of truth as `NitroStackMcpServer.get_capabilities`, so
        # `server/discover` and real capability negotiation never disagree.
        resources_subscribe = supports_resource_subscriptions(self.protocol_era)
        result = build_discover_result(
            server_name=self.server_config.name,
            server_version=self.server_config.version,
            protocol_version=version,
            resources_subscribe=resources_subscribe,
            advertise_tasks=self._advertise_tasks_extension(),
            advertise_app=has_widgets,
            custom_extensions=self._custom_extensions(),
        )
        meta = dict(result.get("_meta") or {})
        meta["io.modelcontextprotocol/serverInfo"] = {
            "name": self.server_config.name,
            "version": self.server_config.version,
        }
        result["_meta"] = meta
        result.setdefault("resultType", "complete")
        return result

    def _list_endpoint_cache_meta(self) -> Dict[str, Any]:
        return build_list_endpoint_cache_hint_meta()

    def _setup_handlers(self, server: NitroStackMcpServer) -> None:
        server.protocol_era = self.protocol_era

        async def _list_tools(
            ctx: ServerRequestContext,
            _params: Optional[PaginatedRequestParams],
        ):
            session_key = self._visibility_session_key(ctx)
            tools = [
                self._build_tool_definition(entry)
                for entry in self._tools.values()
                if self.visibility_transform.is_visible(entry, session_key)
            ]

            if self._transforms.enabled:
                try:
                    context = self._build_transform_context(request_ctx.get(None), invoker=None)
                    tools = await self._transforms.transform_tools(
                        tools,
                        context,
                    )
                except TransformError as exc:
                    raise MCPError(
                        types.INTERNAL_ERROR,
                        str(exc),
                    ) from exc

            return types.ListToolsResult(
                tools=tools,
                _meta=self._list_endpoint_cache_meta(),
                resultType="complete",
            )

        async def _ping(_ctx: ServerRequestContext, _params: Optional[RequestParams]):
            if self.protocol_era == "legacy":
                return types.EmptyResult()
            raise MCPError(types.METHOD_NOT_FOUND, "ping was removed in MCP 2026-07-28")

        async def _call_tool(ctx: ServerRequestContext, params: CallToolRequestParams):
            token = request_ctx.set(ctx)
            try:
                if not self._tool_visible(params.name, ctx):
                    return self._unknown_tool_result(params.name)
                result = await self._call_tool(
                    params.name, params.arguments or {}, task=params.task
                )
                if isinstance(result, types.CreateTaskResult):
                    task = result.task
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=task.task_id)],
                        structuredContent={
                            "resultType": "task",
                            "task": task.model_dump(by_alias=True),
                        },
                        isError=False,
                    )
                return result.model_copy(update={"result_type": result.result_type or "complete"})
            finally:
                request_ctx.reset(token)

        async def _list_resources(
            _ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]
        ):
            return types.ListResourcesResult(
                resources=[
                    self._build_resource_definition(entry) for entry in self._resources.values()
                ],
                _meta=self._list_endpoint_cache_meta(),
                resultType="complete",
            )

        async def _list_resource_templates(
            _ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]
        ):
            return types.ListResourceTemplatesResult(
                resource_templates=[
                    self._build_resource_template_definition(entry)
                    for entry in self._resource_templates
                ],
                resultType="complete",
            )

        async def _read_resource(ctx: ServerRequestContext, params: ReadResourceRequestParams):
            uri = str(params.uri)
            return _read_contents_to_result(uri, await self._read_resource(uri))

        async def _subscribe_resource(_ctx: ServerRequestContext, params: SubscribeRequestParams):
            if not supports_resource_subscriptions(self.protocol_era):
                message = deprecated_method_message("resources/subscribe")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            if str(params.uri) not in self._resources:
                raise ResourceNotFoundError(str(params.uri))
            return types.EmptyResult()

        async def _unsubscribe_resource(
            _ctx: ServerRequestContext, _params: UnsubscribeRequestParams
        ):
            if not supports_resource_subscriptions(self.protocol_era):
                raise MCPError(
                    types.METHOD_NOT_FOUND, "Resource subscriptions are not supported in this era"
                )
            return types.EmptyResult()

        async def _list_prompts(
            _ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]
        ):
            return types.ListPromptsResult(
                prompts=[self._build_prompt_definition(entry) for entry in self._prompts.values()],
                _meta=self._list_endpoint_cache_meta(),
                resultType="complete",
            )

        async def _get_prompt(ctx: ServerRequestContext, params: GetPromptRequestParams):
            arguments = params.arguments or {}
            result = await self._get_prompt(
                params.name,
                {str(key): str(value) for key, value in arguments.items()},
            )
            return result.model_copy(update={"result_type": result.result_type or "complete"})

        server.add_request_handler("ping", RequestParams, _ping)

        async def _discover(ctx: ServerRequestContext, _params: Optional[RequestParams]):
            return self.handle_server_discover(getattr(ctx, "protocol_version", None))

        server.add_request_handler("server/discover", RequestParams, _discover)
        server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
        server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)
        server.add_request_handler("resources/list", PaginatedRequestParams, _list_resources)
        server.add_request_handler(
            "resources/templates/list", PaginatedRequestParams, _list_resource_templates
        )
        server.add_request_handler("resources/read", ReadResourceRequestParams, _read_resource)
        server.add_request_handler(
            "resources/subscribe", SubscribeRequestParams, _subscribe_resource
        )
        server.add_request_handler(
            "resources/unsubscribe", UnsubscribeRequestParams, _unsubscribe_resource
        )
        server.add_request_handler("prompts/list", PaginatedRequestParams, _list_prompts)
        server.add_request_handler("prompts/get", GetPromptRequestParams, _get_prompt)

        async def handle_list_tools(req=None):
            ctx = req if req is not None and hasattr(req, "session") else request_ctx.get(None)
            return ServerResult(await _list_tools(ctx, None))

        async def handle_call_tool(req=None):
            params = getattr(req, "params", None) if req is not None else None
            name = getattr(params, "name", "") if params is not None else ""
            arguments = (getattr(params, "arguments", None) or {}) if params is not None else {}
            task = getattr(params, "task", None) if params is not None else None
            ctx = req if req is not None and hasattr(req, "session") else request_ctx.get(None)
            if not self._tool_visible(name, ctx):
                return ServerResult(self._unknown_tool_result(name))
            token = request_ctx.set(ctx)
            try:
                return ServerResult(await self._call_tool(name, arguments, task=task))
            finally:
                request_ctx.reset(token)

        async def handle_list_resources(req=None):
            return ServerResult(await _list_resources(None, None))

        async def handle_list_resource_templates(req=None):
            return ServerResult(await _list_resource_templates(None, None))

        async def handle_read_resource(req):
            params = getattr(req, "params", req)
            uri = str(params.uri)
            return ServerResult(_read_contents_to_result(uri, await self._read_resource(uri)))

        async def handle_list_prompts(req=None):
            return ServerResult(await _list_prompts(None, None))

        async def handle_get_prompt(req):
            params = getattr(req, "params", req)
            arguments = getattr(params, "arguments", None) or {}
            return ServerResult(
                await self._get_prompt(
                    params.name,
                    {str(key): str(value) for key, value in arguments.items()},
                )
            )

        server.request_handlers[types.ListToolsRequest] = handle_list_tools
        server.request_handlers[types.CallToolRequest] = handle_call_tool
        server.request_handlers[types.ListResourcesRequest] = handle_list_resources
        server.request_handlers[types.ListResourceTemplatesRequest] = handle_list_resource_templates
        server.request_handlers[types.ReadResourceRequest] = handle_read_resource
        server.request_handlers[types.ListPromptsRequest] = handle_list_prompts
        server.request_handlers[types.GetPromptRequest] = handle_get_prompt

        self._register_task_handlers(server)
        self._register_initialized_handler(server)
        server.middleware.insert(1, self._request_scope_middleware)

    def _build_transform_context(self, rc: Any, *, invoker: Any) -> TransformContext:
        """TransformContext for the in-flight request, identity from ``caller_identity``."""
        session_id, subject = caller_identity(rc, self.protocol_era)
        session_key = VisibilityTransform.session_key(session_id, subject)
        exec_ctx = None
        if rc is not None:
            correlation_id, jsonrpc_id = _bind_correlation(rc)
            exec_ctx = ExecutionContext(
                request_id=correlation_id,
                correlation_id=correlation_id,
                jsonrpc_id=jsonrpc_id,
                metadata=dict(_auth_metadata_from_request_ctx(rc)),
                trace=_trace_context_from_request_ctx(rc),
            )
            _apply_request_envelope(exec_ctx, rc)
            self._configure_visibility_context(exec_ctx, rc)
        visible_tools = [
            self._build_tool_definition(entry)
            for entry in self._tools.values()
            if self.visibility_transform.is_visible(entry, session_key)
        ]
        return TransformContext(
            session_id=session_id,
            subject=subject,
            execution_context=exec_ctx,
            visible_tools=visible_tools,
            _invoker=invoker,
        )

    async def _request_scope_middleware(self, ctx: ServerRequestContext, call_next):
        """Bind ``request_ctx`` once for every handler and gate the 2025 handshake."""
        if ctx.method == "initialize" and rejects_legacy_initialize(self.protocol_era):
            raise MCPError(types.METHOD_NOT_FOUND, "Method not found: initialize")
        token = request_ctx.set(ctx)
        try:
            return await call_next(ctx)
        finally:
            request_ctx.reset(token)

    def create_configured_mcp_server(self) -> NitroStackMcpServer:
        """
        Build a fresh, fully-wired low-level server instance sharing this
        application's tool/resource/prompt registries. A single shared server is
        sufficient for the Python `mcp` SDK's HTTP session managers (unlike the TS
        SDK's `Server`, `.run()` here does not bind a transport for the instance's
        lifetime), but this factory is kept available for isolated/stateless use
        cases (e.g. Phase 3 extensions) that want their own server instance.
        """
        server = NitroStackMcpServer(
            name=self.server_config.name, version=self.server_config.version
        )
        self._setup_handlers(server)
        return server
