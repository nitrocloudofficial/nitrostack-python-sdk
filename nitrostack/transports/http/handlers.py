"""Route handlers: landing page, widget preview, health, discovery stubs."""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from pydantic_core import PydanticUndefined
from starlette.responses import HTMLResponse, JSONResponse, Response

from nitrostack.protocol.version import ProtocolEra, protocol_version_for_era
from nitrostack.transports.http.constants import _PROCESS_START, logger
from nitrostack.transports.landing_page import render_landing_page
from nitrostack.transports.proxy import public_url_for_request
from nitrostack.widgets.preview_page import render_preview_page

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication
    from nitrostack.transports.http.middleware import SessionCapMiddleware


def _server_meta(mcp_app: "McpApplication") -> Dict[str, str]:
    cfg = getattr(mcp_app, "server_config", None)
    return {
        "name": getattr(cfg, "name", None) or "NitroStack MCP Server",
        "version": getattr(cfg, "version", None) or "1.0.0",
    }


def _landing_tools(mcp_app: "McpApplication") -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for entry in getattr(mcp_app, "_tools", {}).values():
        try:
            schema = mcp_app._tool_input_schema(entry.input_model)
        except Exception:
            logger.exception("Landing page schema failed for %s", entry.config.name)
            schema = None
        tools.append(
            {
                "name": entry.config.name,
                "description": entry.config.description or "",
                "inputSchema": schema,
            }
        )
    return tools


def _preview_default_arguments(entry: Any) -> Dict[str, Any]:
    """Prefill live preview with tool examples (shopId, openNow, …)."""
    examples = getattr(getattr(entry, "config", None), "examples", None)
    raw = getattr(examples, "input", None) if examples is not None else None
    if isinstance(raw, dict):
        return dict(raw)
    model = getattr(entry, "input_model", None)
    if model is None:
        return {}
    data: Dict[str, Any] = {}
    for fname, field in getattr(model, "model_fields", {}).items():
        default = getattr(field, "default", PydanticUndefined)
        if default is not PydanticUndefined and default is not None:
            data[fname] = default
            continue
        if fname in ("shopId", "shop_id"):
            data[fname] = "tonys-pizza"
        elif fname == "product_id":
            data[fname] = "sku-1"
        elif fname == "filter":
            data[fname] = "all"
        elif fname == "openNow":
            data[fname] = True
        elif fname == "origin":
            data[fname] = "JFK"
        elif fname == "destination":
            data[fname] = "LAX"
        elif fname in ("departureDate", "departure_date"):
            data[fname] = "2026-09-15"
        elif fname in ("offerId", "offer_id"):
            data[fname] = "off_mock123456"
        elif fname in ("orderId", "order_id"):
            data[fname] = "ord_mock123456"
        elif fname == "query":
            data[fname] = "London"
    return data


def make_handlers(
    mcp_app: "McpApplication",
    *,
    endpoint: str,
    protocol_era: ProtocolEra,
    http_engine: str,
    stateless: bool,
    json_response: bool,
    session_cap: Optional["SessionCapMiddleware"],
) -> Dict[str, Callable]:
    """Build the route handlers. Bodies are unchanged from the original
    `build_http_app`; they close over the arguments above."""

    def _request_public_mcp_url(request) -> str:
        port = os.environ.get("PORT") or os.environ.get("MCP_SERVER_PORT") or "3000"
        return public_url_for_request(
            request,
            path=endpoint.rstrip("/") or "/mcp",
            fallback_host=f"localhost:{port}",
        )

    async def health_check(request):
        return JSONResponse(
            {
                "status": "ok",
                "transport": "streamable-http",
                "protocolVersion": protocol_version_for_era(protocol_era),
                "protocolEra": protocol_era,
                "statelessCapable": http_engine == "sessionless",
                "stateless": stateless,
                "jsonResponse": json_response,
                "sessions": session_cap.active_session_count if session_cap else None,
                "uptimeSeconds": round(time.monotonic() - _PROCESS_START, 2),
                "publicUrl": _request_public_mcp_url(request),
            }
        )

    async def root_page(request):
        meta = _server_meta(mcp_app)
        return HTMLResponse(
            render_landing_page(
                name=meta["name"],
                version=meta["version"],
                description=getattr(getattr(mcp_app, "server_config", None), "description", None),
                mcp_endpoint=_request_public_mcp_url(request),
                tools=_landing_tools(mcp_app),
            )
        )

    async def oauth_not_supported(request):
        """Inspector DCR posts `/register` when Authentication is on.

        Return JSON (not the HTML 404 page) so the client shows a clear
        OAuth-off message instead of `Unexpected token '<'`.
        """
        connect_url = _request_public_mcp_url(request)
        return JSONResponse(
            {
                "error": "invalid_request",
                "error_description": (
                    "This MCP server does not use OAuth. In MCP Inspector turn "
                    "Authentication off, then connect with Streamable HTTP to "
                    f"{connect_url}."
                ),
            },
            status_code=404,
        )

    async def widgets_preview(request):
        catalog = []
        for name, entry in getattr(mcp_app, "_tools", {}).items():
            component = getattr(entry, "component", None)
            if component is None:
                continue
            catalog.append(
                {
                    "name": name,
                    "resourceUri": component.resource_uri,
                    "arguments": _preview_default_arguments(entry),
                }
            )
        return HTMLResponse(render_preview_page(catalog))

    async def widgets_preview_call(request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"}, status_code=400)
        name = body.get("tool") or ""
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            return JSONResponse({"error": "arguments must be an object"}, status_code=400)
        entry = getattr(mcp_app, "_tools", {}).get(name)
        if entry is None or getattr(entry, "component", None) is None:
            return JSONResponse({"error": f"No widget tool named {name!r}"}, status_code=404)
        try:
            result = await mcp_app._call_tool(name, arguments)
        except Exception as exc:
            logger.exception("Widget preview tool call failed for %s", name)
            return JSONResponse({"error": str(exc)}, status_code=400)
        structured = getattr(result, "structured_content", None)
        if structured is None:
            structured = getattr(result, "structuredContent", None)
        try:
            html = entry.component.html_with_data(structured)
        except Exception as exc:
            logger.exception("Widget preview render failed for %s", name)
            return JSONResponse(
                {
                    "error": f"Widget render failed: {exc}",
                    "structuredContent": structured,
                    "html": None,
                    "resourceUri": entry.component.resource_uri,
                    "isError": True,
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "structuredContent": structured,
                "html": html,
                "resourceUri": entry.component.resource_uri,
                "isError": bool(getattr(result, "is_error", None) if getattr(result, "is_error", None) is not None else getattr(result, "isError", False)),
            }
        )

    async def json_version(request):
        """Chrome/Cursor DevTools probe `/json/version` when a tab opens localhost."""
        meta = _server_meta(mcp_app)
        health_path = f"{endpoint.rstrip('/')}/health"
        return JSONResponse(
            {
                "Browser": meta["name"],
                "Protocol-Version": protocol_version_for_era(protocol_era),
                "User-Agent": f"NitroStack/{meta['version']}",
                "webSocketDebuggerUrl": "",
                "transport": "mcp",
                "publicUrl": _request_public_mcp_url(request),
                "endpoints": {
                    "mcp": endpoint,
                    "sse": "/sse",
                    "health": health_path,
                },
            }
        )

    async def json_list(request):
        """Chrome DevTools `/json` and `/json/list` expect a page list; empty = not CDP."""
        return JSONResponse([])

    async def favicon(request):
        return Response(status_code=204)

    return {
        "request_public_mcp_url": _request_public_mcp_url,
        "health_check": health_check,
        "root_page": root_page,
        "oauth_not_supported": oauth_not_supported,
        "widgets_preview": widgets_preview,
        "widgets_preview_call": widgets_preview_call,
        "json_version": json_version,
        "json_list": json_list,
        "favicon": favicon,
    }