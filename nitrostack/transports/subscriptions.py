"""HTTP attach point for the official MCP 2026 subscription bus."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Mapping, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from nitrostack.auth.jwt import JWTService
from nitrostack.auth.request import authorization_token_from_headers, verify_bearer_payload
from nitrostack.core.di import DIContainer
from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION
from nitrostack.transports.headers import (
    MCP_SSE_CONTENT_TYPE,
    build_sse_stream_headers,
    get_header,
)
from nitrostack.transports.sse import format_sse_message

try:
    from mcp.shared.subscriptions import (
        SUBSCRIPTION_ID_META_KEY,
        event_matches,
        event_to_notification,
    )
    from mcp_types import SubscriptionFilter
except ImportError:  # pragma: no cover
    SUBSCRIPTION_ID_META_KEY = "io.modelcontextprotocol/subscriptionId"
    event_matches = None  # type: ignore[assignment]
    event_to_notification = None  # type: ignore[assignment]
    SubscriptionFilter = None  # type: ignore[assignment]


def http_listen_requires_auth() -> bool:
    """True when ``/mcp`` is credential-gated (JWT registered or OAuth required)."""
    from nitrostack.auth.oauth import OAuthService, is_oauth_required

    container = DIContainer.get_instance()
    if container.has_value(JWTService):
        return True
    return container.has_value(OAuthService) and is_oauth_required()


def unauthorized_listen_response() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def listen_auth_error(headers: Mapping[str, str]) -> Optional[JSONResponse]:
    """Deny listen with the same credential rules as a gated ``/mcp`` call."""
    if not http_listen_requires_auth():
        return None
    token = authorization_token_from_headers(headers)
    if not token:
        return unauthorized_listen_response()
    if verify_bearer_payload(token) is None:
        return unauthorized_listen_response()
    return None


def _accepts_sse(headers: Mapping[str, str]) -> bool:
    accept = (get_header(headers, "Accept") or "*/*").lower()
    return "text/event-stream" in accept or "*/*" in accept


def _default_filter() -> Any:
    if SubscriptionFilter is None:
        return None
    return SubscriptionFilter(
        tools_list_changed=True,
        prompts_list_changed=True,
        resources_list_changed=True,
    )


def _filter_from_body(body: Any) -> Any:
    honored = _default_filter()
    if SubscriptionFilter is None or not isinstance(body, dict):
        return honored
    params = body.get("params") if isinstance(body.get("params"), dict) else body
    raw = params.get("notifications") if isinstance(params, dict) else None
    if not isinstance(raw, dict):
        return honored
    return SubscriptionFilter(
        tools_list_changed=True if raw.get("toolsListChanged") or raw.get("tools_list_changed") else None,
        prompts_list_changed=True if raw.get("promptsListChanged") or raw.get("prompts_list_changed") else None,
        resources_list_changed=True
        if raw.get("resourcesListChanged") or raw.get("resources_list_changed")
        else None,
        resource_subscriptions=list(
            raw.get("resourceSubscriptions") or raw.get("resource_subscriptions") or []
        )
        or None,
    )


def _notification_payload(event: Any, meta: dict[str, Any]) -> dict[str, Any]:
    if event_to_notification is None:
        return {"method": "notifications/tools/list_changed", "params": {"_meta": meta}}
    notification = event_to_notification(event, meta)
    return notification.model_dump(by_alias=True, exclude_none=True)


def _ack_payload(subscription_id: str, honored: Any) -> dict[str, Any]:
    notifications: dict[str, Any] = {}
    if honored is not None:
        if getattr(honored, "tools_list_changed", None):
            notifications["toolsListChanged"] = True
        if getattr(honored, "prompts_list_changed", None):
            notifications["promptsListChanged"] = True
        if getattr(honored, "resources_list_changed", None):
            notifications["resourcesListChanged"] = True
        uris = getattr(honored, "resource_subscriptions", None)
        if uris:
            notifications["resourceSubscriptions"] = list(uris)
    else:
        notifications = {
            "toolsListChanged": True,
            "promptsListChanged": True,
            "resourcesListChanged": True,
        }
    return {
        "method": "notifications/subscriptions/acknowledged",
        "params": {
            "_meta": {SUBSCRIPTION_ID_META_KEY: subscription_id},
            "notifications": notifications,
        },
    }


def subscriptions_listen_endpoint(mcp_app: Any):
    """GET/POST ``/subscriptions/listen``: SSE attach on the official v2 bus."""

    async def handle(request: Request) -> Response:
        denied = listen_auth_error(request.headers)
        if denied is not None:
            return denied
        if not _accepts_sse(request.headers):
            return Response(status_code=406)

        body: Any = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}

        bus = getattr(getattr(mcp_app, "mcp_server", None), "subscription_bus", None)
        if bus is None:
            return JSONResponse({"error": "subscriptions are not available"}, status_code=503)

        honored = _filter_from_body(body)
        honored_uris = frozenset(getattr(honored, "resource_subscriptions", None) or ())
        subscription_id = str(uuid.uuid4())
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def deliver(event: Any) -> None:
            if event_matches is not None and honored is not None:
                if not event_matches(honored, honored_uris, event):
                    return
            try:
                queue.put_nowait(event)
            except Exception:
                pass

        unsubscribe = bus.subscribe(deliver)

        async def frames():
            try:
                yield format_sse_message(_ack_payload(subscription_id, honored))
                while True:
                    event = await queue.get()
                    yield format_sse_message(
                        _notification_payload(
                            event, {SUBSCRIPTION_ID_META_KEY: subscription_id}
                        )
                    )
            finally:
                unsubscribe()

        return StreamingResponse(
            frames(),
            media_type=MCP_SSE_CONTENT_TYPE,
            headers=build_sse_stream_headers(
                protocol_version=getattr(
                    request.app.state, "protocol_version", MODERN_PROTOCOL_VERSION
                )
                if hasattr(request.app, "state")
                else MODERN_PROTOCOL_VERSION
            ),
        )

    return handle
