"""MCP transport adapters (stdio, stateless HTTP)."""

from nitrostack.transports.dispatch import (
    DispatchStage,
    IngressContext,
    StatelessIngressPipeline,
    is_task_wire_interception,
)
from nitrostack.transports.middleware import StatelessTransportMiddleware, wrap_stateless_transport
from nitrostack.transports.sse import format_sse_message, sse_connect_headers, sse_notification
from nitrostack.transports.subscriptions import (
    http_listen_requires_auth,
    listen_auth_error,
    subscriptions_listen_endpoint,
)

__all__ = [
    "DispatchStage",
    "IngressContext",
    "StatelessIngressPipeline",
    "StatelessTransportMiddleware",
    "wrap_stateless_transport",
    "is_task_wire_interception",
    "format_sse_message",
    "sse_connect_headers",
    "sse_notification",
    "http_listen_requires_auth",
    "listen_auth_error",
    "subscriptions_listen_endpoint",
]
