"""Invocation implementation mixin."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import mcp.types as types
from mcp import MCPError

from nitrostack.core.app_context import (
    _apply_request_envelope,
    _auth_metadata_from_request_ctx,
    _bind_correlation,
    _http_headers_from_request_ctx,
    _tool_arguments_with_mcp_params,
    _trace_context_from_request_ctx,
)
from nitrostack.core.app_schema import parse_tool_input
from nitrostack.core.context import ExecutionContext, TaskContext
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import (
    TaskAlreadyTerminalError,
    TaskExpiredError,
)
from nitrostack.core.interceptors.data_spillover import (
    DataSpilloverInterceptor,
    bind_spillover_context,
)
from nitrostack.core.pipeline import run_pipeline
from nitrostack.core.visibility import VisibilityTransform
from nitrostack.protocol.meta import (
    strip_tool_arguments,
)
from nitrostack.protocol.mrtr import InputRequiredResult, split_mrtr_from_arguments
from nitrostack.protocol.tasks import (
    DEFAULT_TASK_TTL_MS,
    task_support_forbidden_message,
    task_support_required_message,
)
from nitrostack.runtime.caller import caller_identity
from nitrostack.runtime.request_ctx import request_ctx
from nitrostack.tasks.authorization import extract_task_access_context
from nitrostack.transforms import (
    SyntheticTool,
    ToolArgumentError,
    TransformContext,
    validate_tool_arguments,
)
from nitrostack.transports.headers import (
    extract_mcp_param_headers,
)

logger = logging.getLogger(__name__)


class InvocationMixin:
    def _has_spillover(self, method: Callable) -> bool:
        container = DIContainer.get_instance()
        return any(
            isinstance(
                container.resolve(item) if isinstance(item, type) else item,
                DataSpilloverInterceptor,
            )
            for item in getattr(method, "_mcp_interceptors", [])
        )

    async def _bind_spillover(self, context: ExecutionContext, rc: Any) -> None:
        if not self._spillover.routes:
            return
        # OAuth guards may verify opaque tokens as well as the shared JWT path.
        from nitrostack.auth.oauth import OAuthService
        from nitrostack.auth.request import (
            auth_context_from_payload,
            authorization_token_from_request,
        )

        token = authorization_token_from_request(rc)
        container = DIContainer.get_instance()
        if context.auth is None and token and container.has_value(OAuthService):
            try:
                payload = await container.resolve(OAuthService).introspect_token(token)
                if payload.get("active"):
                    context.auth = auth_context_from_payload(payload)
            except Exception:
                pass  # Invalid credentials remain denied by caller_owner.
        bind_spillover_context(context, rc, self._spillover, self.protocol_era)

    def _pipeline_stages(
        self, method: Callable
    ) -> Tuple[List[Any], List[Any], List[Any], List[Any], List[Any]]:
        return (
            getattr(method, "_mcp_guards", []),
            getattr(method, "_mcp_middleware", []),
            getattr(method, "_mcp_interceptors", []),
            getattr(method, "_mcp_pipes", []),
            getattr(method, "_mcp_filters", []),
        )

    def _base_lookup(self, name: str):
        return self._tools.get(name)

    async def _invoke_for_transform(self, name: str, arguments: dict, tctx: TransformContext):
        # Re-enter the normal call path so guards and validation run for the real tool.
        return await self._call_tool(name, arguments, _tctx=tctx)

    def _visibility_session_key(self, request_context: Any) -> tuple[str, str] | None:
        session_id, subject = caller_identity(request_context, self.protocol_era)
        return VisibilityTransform.session_key(session_id, subject)

    def _tool_visible(self, name: str, request_context: Any) -> bool:
        entry = self._tools.get(name)
        if entry is None:
            # Transform-defined synthetic tools are not in the app registry.
            # Let the transform resolver decide whether such names exist.
            return True
        return self.visibility_transform.is_visible(
            entry, self._visibility_session_key(request_context)
        )

    def _configure_visibility_context(
        self, context: ExecutionContext, request_context: Any
    ) -> None:
        key = self._visibility_session_key(request_context)
        context._tool_visibility_controller = self.visibility_transform
        context._visibility_session_key = key
        context._visibility_app = self
        context._visibility_supported = key is not None
        context._visibility_change_callback = self._publish_tools_changed

    @staticmethod
    def _unknown_tool_result(name: str) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Tool '{name}' not found")],
            isError=True,
        )

    def _publish_tools_changed(self, context: ExecutionContext) -> None:
        session = getattr(request_ctx.get(None), "session", None)
        sender = getattr(session, "send_notification", None) if session is not None else None
        if sender is None:
            logger.debug("Transport does not support tools/list_changed notification")
            return

        async def send_notification() -> None:
            try:
                await sender(types.ToolListChangedNotification())
            except Exception:
                logger.debug(
                    "Unable to send session tools/list_changed notification", exc_info=True
                )

        try:
            asyncio.create_task(send_notification())
        except RuntimeError:
            logger.debug("No running event loop for tools/list_changed notification")

    async def _call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        task: Any = None,
        _tctx: Optional[TransformContext] = None,
    ):
        """Resolve through the transform pipeline, then run the real tool path."""
        if not self._transforms.enabled:
            return await self._call_real_tool(name, arguments, task=task)

        tctx = _tctx or self._build_transform_context(
            request_ctx.get(None), invoker=self._invoke_for_transform
        )
        try:
            resolved = await self._transforms.resolve_tool(name, self._base_lookup, tctx)
        except Exception as exc:
            logger.exception("Transform failed resolving tool %s", name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], isError=True
            )

        if isinstance(resolved, SyntheticTool):
            payload = arguments or {}
            inner = payload.get("input")
            if (
                isinstance(inner, dict)
                and set(payload) <= {"input"}
                and "input" not in resolved.input_model.model_fields
            ):
                payload = inner  # accept the older {"input": {...}} wrap
            try:
                validated = validate_tool_arguments(resolved.name, resolved.input_model, payload)
                result = await resolved.handler(validated, tctx)
            except ToolArgumentError as exc:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"{exc}: {exc.errors}")],
                    isError=True,
                )
            except Exception as exc:
                logger.exception("Synthetic tool %s failed", name)
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(exc))], isError=True
                )
            return self._to_call_tool_result(result)

        return await self._call_real_tool(name, arguments, task=task)

    async def _call_real_tool(self, name: str, arguments: Dict[str, Any], task: Any = None):
        entry = self._tools.get(name)
        if entry is None:
            return self._unknown_tool_result(name)
        if not self._tool_visible(name, request_ctx.get(None)):
            return self._unknown_tool_result(name)

        cfg = entry.config
        tool_arguments, input_responses, request_state = split_mrtr_from_arguments(
            strip_tool_arguments(arguments)
        )
        rc = request_ctx.get(None)
        tool_arguments = _tool_arguments_with_mcp_params(
            tool_arguments,
            extract_mcp_param_headers(_http_headers_from_request_ctx(rc)),
            entry.input_model,
        )
        # Pydantic validates after accepting either Inspector top-level fields
        # or the older `{input: {...}}` wrap. Low-level jsonschema is off
        # (`validate_input=False`) so the wrap is not rejected against the
        # published top-level inputSchema.
        input_instance = parse_tool_input(entry.input_model, tool_arguments)
        guards, middleware, interceptors, pipes, filters = self._pipeline_stages(entry.method)

        # Task metadata: explicit ``params.task``, then the 1.x experimental
        # slot, then the official request context ``params`` mapping.
        task_metadata = task
        session = None
        progress_token = None
        if rc is not None:
            if task_metadata is None and getattr(rc, "experimental", None) is not None:
                task_metadata = rc.experimental.task_metadata
            params = getattr(rc, "params", None)
            if task_metadata is None:
                task_metadata = getattr(params, "task", None)
            if task_metadata is None and isinstance(params, Mapping):
                task_metadata = params.get("task")
            meta = getattr(rc, "meta", None)
            if isinstance(meta, Mapping):
                progress_token = meta.get("progress_token") or meta.get("progressToken")
            elif meta is not None:
                progress_token = getattr(meta, "progress_token", None) or getattr(
                    meta, "progressToken", None
                )
            session = getattr(rc, "session", None)
        auth_meta = _auth_metadata_from_request_ctx(rc)
        trace = _trace_context_from_request_ctx(rc)

        is_task = task_metadata is not None
        if self.protocol_era in ("auto", "modern") and is_task:
            raise MCPError(
                types.INVALID_PARAMS,
                "Server does not support task augmentation on the MCP 2026 wire",
            )
        if cfg.task_support == "forbidden" and task_metadata is not None:
            raise MCPError(
                types.METHOD_NOT_FOUND,
                task_support_forbidden_message(cfg.name),
            )
        if cfg.task_support == "required" and task_metadata is None:
            raise MCPError(
                types.INVALID_REQUEST,
                task_support_required_message(cfg.name),
            )

        if is_task:
            ttl_ms = (
                task_metadata.ttl
                if task_metadata and task_metadata.ttl is not None
                else DEFAULT_TASK_TTL_MS
            )
            task_access = extract_task_access_context(rc, self.protocol_era)
            task = await self.task_manager.create_task(
                ttl_ms=ttl_ms,
                tool_name=cfg.name,
                owner_id=task_access.user_id if task_access else None,
                tenant_id=task_access.tenant_id if task_access else None,
                session_id=task_access.session_id if task_access else None,
            )
            task_id = task.id
            correlation_id, jsonrpc_id = _bind_correlation(rc)

            async def background_execution():
                task_ctx = ExecutionContext(
                    request_id=correlation_id,
                    correlation_id=correlation_id,
                    jsonrpc_id=jsonrpc_id,
                    tool_name=cfg.name,
                    metadata={"input": input_instance, **auth_meta},
                    input_responses=input_responses,
                    request_state=request_state,
                    trace=trace,
                )
                _apply_request_envelope(task_ctx, rc)
                self._configure_visibility_context(task_ctx, rc)
                await self._bind_spillover(task_ctx, rc)
                task_ctx.task = TaskContext(
                    task_id,
                    self.task_manager,
                    session=session,
                    progress_token=progress_token,
                    correlation_id=correlation_id,
                )
                try:
                    result = await run_pipeline(
                        handler=entry.method,
                        handler_instance=entry.instance,
                        args=(input_instance, task_ctx),
                        kwargs={},
                        context=task_ctx,
                        guards=guards,
                        middleware=middleware,
                        interceptors=interceptors,
                        pipes=pipes,
                        filters=filters,
                        param_name="input",
                        param_type=entry.input_model,
                    )
                    if isinstance(result, InputRequiredResult):
                        await self.task_manager.require_input(
                            task_id,
                            result.to_wire_dict(),
                            progress=result.message or "Additional input required",
                        )
                    else:
                        await self.task_manager.complete_task(
                            task_id, self._to_call_tool_result(result, entry.component, task_ctx)
                        )
                except Exception as e:
                    try:
                        await self.task_manager.fail_task(task_id, e)
                    except (TaskAlreadyTerminalError, TaskExpiredError):
                        # Cancelled/expired while running — leave terminal state as-is.
                        pass

            asyncio.create_task(background_execution())
            return types.CreateTaskResult(task=self._task_data_to_mcp_task(task))

        correlation_id, jsonrpc_id = _bind_correlation(rc)
        ctx = ExecutionContext(
            request_id=correlation_id,
            correlation_id=correlation_id,
            jsonrpc_id=jsonrpc_id,
            tool_name=cfg.name,
            metadata={"input": input_instance, **auth_meta},
            input_responses=input_responses,
            request_state=request_state,
            trace=trace,
        )
        _apply_request_envelope(ctx, rc)
        self._configure_visibility_context(ctx, rc)
        await self._bind_spillover(ctx, rc)
        ticket = self._in_flight.register(correlation_id, jsonrpc_id=jsonrpc_id)
        try:
            result = await run_pipeline(
                handler=entry.method,
                handler_instance=entry.instance,
                args=(input_instance, ctx),
                kwargs={},
                context=ctx,
                guards=guards,
                middleware=middleware,
                interceptors=interceptors,
                pipes=pipes,
                filters=filters,
                param_name="input",
                param_type=entry.input_model,
            )
        except Exception as exc:
            logger.exception("Tool %s failed", cfg.name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                isError=True,
            )
        finally:
            self._in_flight.discard(correlation_id)
        if ticket.cancel_requested.is_set():
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Request was cancelled.")],
                isError=True,
            )
        return self._to_call_tool_result(result, entry.component, ctx)
