"""TS template widget map must be exposed by every Python project copy.

TypeScript ``@Widget`` tools:
  starter   calculate            -> calculator-result
  pizzaz    show_pizza_map/list/shop
  oauth     search_flights, get_flight_details, search_airports,
            create_order, get_order_details, get_seat_map, cancel_order

Tools that must stay widget-free (same as TS): convert_temperature, get_airlines.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import mcp.types as types

from nitrostack.core.di import DIContainer
from nitrostack.testing import NitroTestingModule

ROOT = Path(__file__).resolve().parent.parent

TS_WIDGETS = {
    "starter": {
        "calculate": "calculator-result",
    },
    "pizzaz": {
        "show_pizza_map": "pizza-map",
        "show_pizza_list": "pizza-list",
        "show_pizza_shop": "pizza-shop",
    },
    "flight-booking": {
        "search_flights": "flight-search-results",
        "get_flight_details": "flight-details",
        "search_airports": "airport-search",
        "create_order": "order-summary",
        "get_order_details": "order-summary",
        "get_seat_map": "seat-selection",
        "cancel_order": "order-cancellation",
    },
}

NO_WIDGET = {
    "starter": ("convert_temperature",),
    "pizzaz": (),
    "flight-booking": ("get_airlines",),
}

CALLS = {
    "calculate": {"operation": "add", "a": 2, "b": 3},
    "show_pizza_map": {"filter": "all"},
    "show_pizza_list": {},
    "show_pizza_shop": {"shopId": "tonys-pizza"},
    "search_flights": {
        "origin": "DEL",
        "destination": "LHR",
        "departureDate": "2026-08-19",
        "adults": 2,
        "cabinClass": "economy",
    },
    "get_flight_details": {"offerId": "off_mock123456"},
    "search_airports": {"query": "Delhi"},
    "create_order": {
        "offerId": "off_mock123456",
        "passengers": '[{"title":"mr","givenName":"Ada","familyName":"Lovelace","gender":"F","bornOn":"1990-01-15","email":"ada@example.com","phoneNumber":"+15550001"}]',
    },
    "get_order_details": {"orderId": "ord_mock123456"},
    "get_seat_map": {"offerId": "off_mock123456"},
    "cancel_order": {"orderId": "ord_mock123456"},
}

PROJECTS = (
    ("starter", ROOT / "nitrostack" / "templates" / "starter", "starter"),
    ("pizzaz", ROOT / "nitrostack" / "templates" / "pizzaz", "pizzaz"),
    ("pizza-app", ROOT / "pizza-app", "pizzaz"),
    ("flight-booking", ROOT / "nitrostack" / "templates" / "flight-booking", "flight-booking"),
    ("flight-book-app", ROOT / "flight-book-app", "flight-booking"),
)


def _purge_local_imports() -> None:
    doomed = [
        key
        for key in list(sys.modules)
        if key == "app_module"
        or key.startswith("app_module.")
        or key in {"modules", "services", "guards", "health"}
        or key.startswith(("modules.", "services.", "guards.", "health."))
    ]
    for key in doomed:
        sys.modules.pop(key, None)


def _tool_meta(tool: types.Tool) -> dict:
    return getattr(tool, "meta", None) or getattr(tool, "_meta", None) or {}


def _boot(project_dir: Path):
    os.environ.pop("OAUTH_REQUIRED", None)
    os.environ.setdefault("NITROSTACK_APP_MODE", "universal")
    os.environ.pop("DUFFEL_API_KEY", None)
    _purge_local_imports()
    DIContainer.reset()
    sys.path.insert(0, str(project_dir))
    try:
        from app_module import AppModule  # type: ignore

        return asyncio.run(NitroTestingModule.create(AppModule))
    finally:
        if sys.path and sys.path[0] == str(project_dir):
            sys.path.pop(0)


def _assert_project(label: str, project_dir: Path, family: str) -> None:
    expected = TS_WIDGETS[family]
    harness = _boot(project_dir)
    list_handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
    tools = asyncio.run(list_handler(None)).root.tools
    by_name = {tool.name: tool for tool in tools}

    list_res = harness.app.mcp_server.request_handlers[types.ListResourcesRequest]
    resources = asyncio.run(list_res(None)).root.resources
    uris = {str(res.uri) for res in resources}

    for name, route in expected.items():
        assert name in by_name, f"{label}: missing tool {name}"
        uri = f"ui://widget/{route}.html"
        meta = _tool_meta(by_name[name])
        assert meta.get("ui", {}).get("resourceUri") == uri, f"{label}: {name} widget meta {meta}"
        assert uri in uris, f"{label}: resources/list missing {uri}"

        call_handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
        raw = asyncio.run(
            call_handler(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments=CALLS[name]),
                )
            )
        )
        payload = raw.root
        assert payload.is_error is not True, f"{label}: {name} error {getattr(payload, 'content', None)}"
        assert payload.structured_content, f"{label}: {name} has no structuredContent"
        embedded = [block for block in payload.content if getattr(block, "type", None) == "resource"]
        assert embedded, f"{label}: {name} tools/call missing EmbeddedResource widget"
        assert str(embedded[0].resource.uri) == uri
        html = embedded[0].resource.text
        assert html and "<html" in html.lower()
        result_meta = getattr(payload, "meta", None) or getattr(payload, "_meta", None) or {}
        assert result_meta.get("ui", {}).get("resourceUri") == uri

    for name in NO_WIDGET[family]:
        if name not in by_name:
            continue
        meta = _tool_meta(by_name[name])
        assert "resourceUri" not in (meta.get("ui") or {}), f"{label}: {name} should not have a widget"


def test_starter_template_matches_ts_widgets():
    _assert_project("starter", ROOT / "nitrostack" / "templates" / "starter", "starter")


def test_pizzaz_template_matches_ts_widgets():
    _assert_project("pizzaz", ROOT / "nitrostack" / "templates" / "pizzaz", "pizzaz")


def test_pizza_app_matches_ts_widgets():
    path = ROOT / "pizza-app"
    if not (path / "app_module.py").is_file():
        return
    _assert_project("pizza-app", path, "pizzaz")


def test_flight_template_matches_ts_widgets():
    _assert_project("flight-booking", ROOT / "nitrostack" / "templates" / "flight-booking", "flight-booking")


def test_flight_book_app_matches_ts_widgets():
    path = ROOT / "flight-book-app"
    if not (path / "app_module.py").is_file():
        return
    _assert_project("flight-book-app", path, "flight-booking")
