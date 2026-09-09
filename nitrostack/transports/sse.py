"""SSE notification bus helpers."""

from __future__ import annotations

import json
from typing import Any

from nitrostack.transports.headers import build_sse_stream_headers


def format_sse_message(payload: dict[str, Any]) -> bytes:
    """Format a JSON notification as an SSE event: message."""
    data = json.dumps(payload, separators=(",", ":"))
    return f"event: message\ndata: {data}\n\n".encode("utf-8")


def sse_notification(method: str, params: dict[str, Any] | None = None) -> bytes:
    """Build an SSE frame for notifications/tools/list_changed or tasks/status."""
    body: dict[str, Any] = {"method": method}
    if params is not None:
        body["params"] = params
    return format_sse_message(body)


def sse_connect_headers() -> dict[str, str]:
    """Headers for GET /mcp Accept: text/event-stream connections."""
    return build_sse_stream_headers()
