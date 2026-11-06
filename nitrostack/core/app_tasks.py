"""TaskProtocol implementation mixin."""

import logging
import sys
import uuid
from typing import Any, Dict, Optional

import mcp.types as types
from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp.types import (
    PaginatedRequestParams,
)
from pydantic import BaseModel

from nitrostack.core.app_config import GetTaskResult, _iso_timestamp
from nitrostack.core.app_context import _apply_request_envelope
from nitrostack.core.context import ExecutionContext
from nitrostack.core.errors import (
    TaskAlreadyTerminalError,
    TaskExpiredError,
    TaskNotFoundError,
)
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.core.pipeline import run_pipeline
from nitrostack.core.task import TaskStatus
from nitrostack.protocol.deprecated import (
    deprecated_method_message,
    rejects_deprecated_method,
)
from nitrostack.runtime.request_ctx import request_ctx
from nitrostack.tasks.authorization import extract_task_access_context

logger = logging.getLogger(__name__)


class TaskProtocolMixin:
    def _task_data_to_mcp_task(self, task) -> types.Task:
        """Map TaskData to MCP Task. EXPIRED is not an MCP wire status — surface as error."""
        if task.status == TaskStatus.EXPIRED:
            raise MCPError(types.INVALID_PARAMS, f"Task {task.id} has expired")
        return types.Task(
            taskId=task.id,
            status=task.status.value,
            statusMessage=task.progress or "",
            createdAt=_iso_timestamp(task.created_at),
            lastUpdatedAt=_iso_timestamp(task.last_updated_at or task.created_at),
            ttl=task.ttl if task.ttl is not None else 0,
            pollInterval=task.poll_interval,
        )

    def _serialize_task_result_payload(self, result: Any) -> Any:
        if isinstance(result, types.CallToolResult):
            return result.model_dump(by_alias=True, exclude_none=True)
        if isinstance(result, BaseModel):
            return result.model_dump()
        return result

    def _build_get_task_result(self, task) -> types.GetTaskResult:
        mcp_task = self._task_data_to_mcp_task(task)
        payload: Dict[str, Any] = {
            "taskId": mcp_task.task_id,
            "status": mcp_task.status,
            "statusMessage": mcp_task.status_message,
            "createdAt": mcp_task.created_at,
            "lastUpdatedAt": mcp_task.last_updated_at,
            "ttl": mcp_task.ttl,
            "pollInterval": mcp_task.poll_interval,
        }
        if task.status == TaskStatus.COMPLETED and task.result is not None:
            payload["result"] = self._serialize_task_result_payload(task.result)
        elif task.status == TaskStatus.FAILED and task.error is not None:
            payload["error"] = {"message": str(task.error)}
        elif task.status == TaskStatus.INPUT_REQUIRED and task.result is not None:
            payload["result"] = task.result
        return GetTaskResult(**payload)

    def _register_task_handlers(self, server: NitroStackMcpServer) -> None:
        if self.protocol_era in ("auto", "modern"):
            server.has_task_support = False
            return

        async def handle_list_tasks(req):
            if rejects_deprecated_method("tasks/list", self.protocol_era):
                message = deprecated_method_message("tasks/list")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            tasks_list = []
            access = extract_task_access_context(request_ctx.get(None), self.protocol_era)
            params = getattr(req, "params", req)
            cursor = getattr(params, "cursor", None) if params is not None else None
            tasks_page, next_cursor = await self.task_manager.list_tasks_page(
                access_context=access,
                cursor=cursor,
            )
            for t in tasks_page:
                if t.status == TaskStatus.EXPIRED:
                    continue
                tasks_list.append(self._task_data_to_mcp_task(t))
            return types.ListTasksResult(tasks=tasks_list, nextCursor=next_cursor)

        async def handle_get_task(req):
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None), self.protocol_era)
            try:
                t = await self.task_manager.get_task(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")  # noqa: B904
            return self._build_get_task_result(t)

        async def handle_cancel_task(req):
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None), self.protocol_era)
            try:
                await self.task_manager.cancel_task(task_id, access_context=access)
                t = await self.task_manager.get_task(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")  # noqa: B904
            except TaskExpiredError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} has expired")  # noqa: B904
            except TaskAlreadyTerminalError as e:
                raise MCPError(types.INVALID_PARAMS, str(e))  # noqa: B904
            mcp_task = self._task_data_to_mcp_task(t)
            return types.CancelTaskResult(
                taskId=mcp_task.task_id,
                status=mcp_task.status,
                statusMessage=mcp_task.status_message,
                createdAt=mcp_task.created_at,
                lastUpdatedAt=mcp_task.last_updated_at,
                ttl=mcp_task.ttl,
                pollInterval=mcp_task.poll_interval,
            )

        async def handle_get_task_payload(req):
            if rejects_deprecated_method("tasks/result", self.protocol_era):
                message = deprecated_method_message("tasks/result")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None), self.protocol_era)
            try:
                t = await self.task_manager.wait_until_done(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")  # noqa: B904
            if t.status == TaskStatus.COMPLETED:
                return t.result
            if t.status == TaskStatus.CANCELLED:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="Task was cancelled.")],
                    isError=True,
                )
            if t.status == TaskStatus.EXPIRED:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="Task has expired.")],
                    isError=True,
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(t.error or t.progress))],
                isError=True,
            )

        async def on_list_tasks(
            ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]
        ):
            return await handle_list_tasks(types.ListTasksRequest(params=params))

        async def on_get_task(ctx: ServerRequestContext, params: types.GetTaskRequestParams):
            return await handle_get_task(types.GetTaskRequest(params=params))

        async def on_cancel_task(ctx: ServerRequestContext, params: types.CancelTaskRequestParams):
            return await handle_cancel_task(types.CancelTaskRequest(params=params))

        async def on_get_task_payload(
            ctx: ServerRequestContext, params: types.GetTaskPayloadRequestParams
        ):
            return await handle_get_task_payload(types.GetTaskPayloadRequest(params=params))

        server.add_request_handler("tasks/list", PaginatedRequestParams, on_list_tasks)
        server.add_request_handler("tasks/get", types.GetTaskRequestParams, on_get_task)
        server.add_request_handler("tasks/cancel", types.CancelTaskRequestParams, on_cancel_task)
        server.add_request_handler(
            "tasks/result", types.GetTaskPayloadRequestParams, on_get_task_payload
        )
        server.request_handlers[types.ListTasksRequest] = handle_list_tasks
        server.request_handlers[types.GetTaskRequest] = handle_get_task
        server.request_handlers[types.CancelTaskRequest] = handle_cancel_task
        server.request_handlers[types.GetTaskPayloadRequest] = handle_get_task_payload
        server.has_task_support = True

    def _register_initialized_handler(self, server: NitroStackMcpServer) -> None:
        async def handle_initialized(notification: types.InitializedNotification):
            for instance, method, config in self._initial_tools:
                try:
                    entry = self._tools[config.name]
                    try:
                        input_inst = entry.input_model()
                    except Exception:
                        input_inst = None

                    ctx = ExecutionContext(
                        request_id=f"initial-tool-{uuid.uuid4().hex[:8]}",
                        tool_name=config.name,
                        metadata={},
                    )
                    _apply_request_envelope(ctx, request_ctx.get(None))
                    guards, middleware, interceptors, pipes, filters = self._pipeline_stages(method)

                    await run_pipeline(
                        handler=method,
                        handler_instance=instance,
                        args=(input_inst, ctx),
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
                except Exception as e:
                    sys.stderr.write(f"Error executing initial tool '{config.name}': {e}\n")
                    sys.stderr.flush()

        async def on_initialized(
            ctx: ServerRequestContext, _params: Optional[types.NotificationParams]
        ):
            await handle_initialized(types.InitializedNotification())

        server.add_notification_handler(
            "notifications/initialized", types.NotificationParams, on_initialized
        )
        server.notification_handlers[types.InitializedNotification] = handle_initialized
