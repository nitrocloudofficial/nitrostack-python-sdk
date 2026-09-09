"""Fan-out for ``notifications/tasks/status`` across live transports."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Protocol

from nitrostack.tasks.authorization import entry_matches_access_context
from nitrostack.tasks.types import TaskAccessContext, TaskEntry

logger = logging.getLogger(__name__)

TASK_STATUS_METHOD = "notifications/tasks/status"


def build_task_status_notification(
    task_id: str,
    status: str,
    *,
    status_message: Optional[str] = None,
) -> dict[str, Any]:
    """Wire notification for a task status or progress change."""
    params: dict[str, Any] = {"taskId": task_id, "status": status}
    if status_message is not None:
        params["statusMessage"] = status_message
    return {"method": TASK_STATUS_METHOD, "params": params}


class TaskStatusSink(Protocol):
    """One connected transport that can receive task status."""

    access: Optional[TaskAccessContext]
    session_id: Optional[str]
    task_id: Optional[str]

    async def send(self, notification: dict[str, Any]) -> None:
        """Deliver one status notification. Must not raise to the router."""
        ...


class CallbackTaskSink:
    """Test and in-process sink that records or forwards notifications."""

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], Any],
        *,
        access: Optional[TaskAccessContext] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        self._callback = callback
        self.access = access
        self.session_id = session_id
        self.task_id = task_id

    async def send(self, notification: dict[str, Any]) -> None:
        result = self._callback(notification)
        if hasattr(result, "__await__"):
            await result


class QueueTaskSink:
    """Push notifications onto an asyncio queue (listen / SSE attach)."""

    def __init__(
        self,
        put: Callable[[dict[str, Any]], None],
        *,
        access: Optional[TaskAccessContext] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        self._put = put
        self.access = access
        self.session_id = session_id
        self.task_id = task_id

    async def send(self, notification: dict[str, Any]) -> None:
        self._put(notification)


class StreamTaskSink:
    """Write a JSON-RPC notification onto an official stream write side."""

    def __init__(
        self,
        write_stream: Any,
        *,
        access: Optional[TaskAccessContext] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        self._write_stream = write_stream
        self.access = access
        self.session_id = session_id
        self.task_id = task_id

    async def send(self, notification: dict[str, Any]) -> None:
        from mcp.shared.message import SessionMessage
        from mcp_types import jsonrpc_message_adapter

        message = jsonrpc_message_adapter.validate_python(
            {
                "jsonrpc": "2.0",
                "method": notification["method"],
                "params": notification.get("params") or {},
            }
        )
        await self._write_stream.send(SessionMessage(message))


class SessionTaskSink:
    """Notify the originating MCP session (stdio or legacy SSE)."""

    def __init__(
        self,
        session: Any,
        *,
        access: Optional[TaskAccessContext] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        self._session = session
        self.access = access
        self.session_id = session_id
        self.task_id = task_id

    async def send(self, notification: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            return
        method = notification["method"]
        params = notification.get("params") or {}
        sender = getattr(session, "send_notification", None)
        if sender is not None:
            await sender(method, params)
            return
        outbound = getattr(session, "outbound", None)
        notify = getattr(outbound, "notify", None) if outbound is not None else None
        if notify is not None:
            await notify(method, params)


def _sink_accepts(sink: TaskStatusSink, entry: TaskEntry) -> bool:
    task_id = getattr(sink, "task_id", None)
    if task_id is not None and task_id != entry.task_id:
        return False
    session_id = getattr(sink, "session_id", None)
    if session_id is not None and entry.session_id and session_id != entry.session_id:
        return False
    access = getattr(sink, "access", None)
    if access is not None and not entry_matches_access_context(entry, access):
        return False
    return True


class TaskStatusRouter:
    """Deliver task status to every live channel. A failed send is ignored."""

    def __init__(self) -> None:
        self._sinks: dict[object, TaskStatusSink] = {}

    def register(self, sink: TaskStatusSink) -> Callable[[], None]:
        token = object()
        self._sinks[token] = sink

        def unsubscribe() -> None:
            self._sinks.pop(token, None)

        return unsubscribe

    async def notify_task_status(
        self,
        entry: TaskEntry,
        *,
        status: Optional[str] = None,
        status_message: Optional[str] = None,
    ) -> None:
        """Fan out one status change. Never raises to the task lifecycle."""
        notification = build_task_status_notification(
            entry.task_id,
            status if status is not None else entry.status,
            status_message=status_message
            if status_message is not None
            else entry.data.status_message,
        )
        for sink in list(self._sinks.values()):
            if not _sink_accepts(sink, entry):
                continue
            try:
                await sink.send(notification)
            except Exception:
                logger.exception("task status notify failed; continuing")
