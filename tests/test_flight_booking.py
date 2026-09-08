"""Flight-booking (oauth) Studio path: no token + mock Duffel + widgets."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mcp.types as types
from pydantic import BaseModel

from nitrostack import (
    ExecutionContext,
    OAuthGuard,
    WidgetOptions,
    injectable,
    module,
    tool,
    use_guards,
    widget,
)
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app, parse_tool_input
from nitrostack.core.di import DIContainer
from nitrostack.testing import NitroTestingModule
from nitrostack.transports.http import _preview_default_arguments, build_http_app
from nitrostack.widgets.route_templates import get_builtin_route_html
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
WIDGET_ROUTES = (
    "flight-search-results",
    "flight-details",
    "airport-search",
    "order-summary",
    "seat-selection",
    "order-cancellation",
)


def test_flight_widget_html_is_registered_for_all_routes():
    out = ROOT / "nitrostack" / "templates" / "flight-booking" / "widgets" / "out"
    for route in WIDGET_ROUTES:
        path = out / f"{route}.html"
        assert path.is_file(), f"missing {path}"
        builtin = get_builtin_route_html(route)
        assert builtin is not None
        assert path.read_text(encoding="utf-8") == builtin


class SearchFlightsInput(BaseModel):
    origin: str
    destination: str
    departureDate: str
    adults: int = 1
    cabinClass: str = "economy"


@injectable()
class _MockDuffel:
    async def search_flights(self, params: dict) -> dict:
        return {
            "id": "orq_mock123456",
            "offers": [
                {
                    "id": "off_mock123456",
                    "total_amount": "450.00",
                    "total_currency": "USD",
                    "slices": [
                        {
                            "origin": {"iata_code": params["origin"]},
                            "destination": {"iata_code": params["destination"]},
                            "duration": "PT6H30M",
                        }
                    ],
                }
            ],
        }


@injectable(deps=[_MockDuffel])
class FlightStudioTools:
    def __init__(self, service: _MockDuffel):
        self.service = service

    @tool(name="search_flights", description="search", input_schema=SearchFlightsInput)
    @use_guards(OAuthGuard)
    @widget(WidgetOptions(route="flight-search-results", prefers_border=True))
    async def search_flights(self, input: SearchFlightsInput, context: ExecutionContext) -> dict:
        return await self.service.search_flights(input.model_dump())


@module(name="flight_studio", controllers=[FlightStudioTools], providers=[_MockDuffel])
class FlightStudioModule:
    pass


def test_studio_can_search_flights_without_token():
    os.environ.pop("OAUTH_REQUIRED", None)

    async def run():
        harness = await NitroTestingModule.create(FlightStudioModule)
        tools = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
        listed = (await tools(None)).root.tools
        target = next(t for t in listed if t.name == "search_flights")
        meta = getattr(target, "meta", None) or getattr(target, "_meta", {}) or {}
        assert meta.get("ui", {}).get("resourceUri") == "ui://widget/flight-search-results.html"

        result = await harness.call_tool(
            "search_flights",
            {"origin": "JFK", "destination": "LAX", "departureDate": "2026-09-15"},
        )
        assert result["offers"][0]["id"] == "off_mock123456"
        assert result["offers"][0]["slices"][0]["origin"]["iata_code"] == "JFK"

        call_handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
        raw = await call_handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="search_flights",
                    arguments={"origin": "JFK", "destination": "LAX", "departureDate": "2026-09-15"},
                ),
            )
        )
        payload = raw.root
        assert payload.isError is not True
        assert payload.structuredContent["offers"]
        html = next(b.resource.text for b in payload.content if getattr(b, "type", None) == "resource")
        assert "JFK" in html
        assert "LAX" in html
        assert "450.00" in html

    asyncio.run(run())


def test_oauth_required_blocks_studio_without_token():
    os.environ["OAUTH_REQUIRED"] = "true"
    try:
        async def run():
            harness = await NitroTestingModule.create(FlightStudioModule)
            raw_handler = harness.app.mcp_server.request_handlers[types.CallToolRequest]
            try:
                resp = await raw_handler(
                    types.CallToolRequest(
                        method="tools/call",
                        params=types.CallToolRequestParams(
                            name="search_flights",
                            arguments={"origin": "JFK", "destination": "LAX", "departureDate": "2026-09-15"},
                        ),
                    )
                )
                assert resp.root.isError is True
                text = resp.root.content[0].text
                assert "OAuth" in text or "Access denied" in text
            except PermissionError as exc:
                assert "OAuth token required" in str(exc)

        asyncio.run(run())
    finally:
        os.environ.pop("OAUTH_REQUIRED", None)


def test_preview_defaults_and_blank_optional_flight_fields():
    class _Entry:
        config = type("C", (), {"examples": None})()
        input_model = SearchFlightsInput

    defaults = _preview_default_arguments(_Entry())
    assert defaults["origin"] == "JFK"
    assert defaults["destination"] == "LAX"
    assert defaults["departureDate"] == "2026-09-15"

    parsed = parse_tool_input(SearchFlightsInput, {"origin": "JFK", "destination": "LAX", "departureDate": "2026-09-15", "cabinClass": ""})
    assert parsed.cabinClass == "economy"


def test_mock_duffel_search_airports_matches_query():
    sys.path.insert(0, str(ROOT / "nitrostack" / "templates" / "flight-booking"))
    from services.duffel_service import DuffelService

    os.environ.pop("DUFFEL_API_KEY", None)
    service = DuffelService()
    assert service.is_mock is True

    async def run():
        jfk = await service.search_airports("JFK")
        assert jfk and jfk[0]["iata_code"] == "JFK"
        lax = await service.search_airports("los angeles")
        assert any(item["iata_code"] == "LAX" for item in lax)
        delhi = await service.search_airports("Delhi")
        assert any(item["iata_code"] == "DEL" for item in delhi)
        assert delhi[0]["iata_code"] == "DEL"
        del_code = await service.search_airports("DEL")
        assert del_code[0]["iata_code"] == "DEL"
        london = await service.search_airports("London")
        codes = {item["iata_code"] for item in london}
        assert {"LHR", "LGW", "STN"} <= codes
        assert await service.search_airports("zzzznotanairport") == []
        flights = await service.search_flights(
            {"origin": "DEL", "destination": "LHR", "departureDate": "2026-08-19", "adults": 2, "cabinClass": "economy"}
        )
        assert flights["offers"][0]["slices"][0]["origin"]["iata_code"] == "DEL"
        assert flights["offers"][0]["slices"][0]["destination"]["iata_code"] == "LHR"
        details = await service.get_offer("off_mock123456")
        assert details["slices"][0]["origin"]["iata_code"] == "DEL"

    asyncio.run(run())


class _FakeDuffelResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps({"data": self._payload}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_live_duffel_requests_target_v2_air_endpoints():
    """A real key must hit Duffel v2: flight resources under /air, places under /places."""
    sys.path.insert(0, str(ROOT / "nitrostack" / "templates" / "flight-booking"))
    from services.duffel_service import DuffelService

    os.environ["DUFFEL_API_KEY"] = "duffel_live_regression_key"
    original_urlopen = urllib.request.urlopen
    captured = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _FakeDuffelResponse({})

    try:
        service = DuffelService()
        assert service.is_mock is False, "non-placeholder key must leave mock mode"
        urllib.request.urlopen = fake_urlopen

        async def run():
            await service.get_airlines()
            await service.search_airports("London")
            await service.search_flights(
                {"origin": "JFK", "destination": "LAX", "departureDate": "2026-10-15", "adults": 1}
            )
            await service.get_offer("off_1")
            await service.get_seats_for_offer("off_1")
            await service.create_order({"selectedOffers": ["off_1"], "passengers": []})
            await service.get_order("ord_1")
            await service.cancel_order("ord_1")

        asyncio.run(run())
    finally:
        urllib.request.urlopen = original_urlopen
        os.environ.pop("DUFFEL_API_KEY", None)

    assert [(r.get_method(), r.full_url) for r in captured] == [
        ("GET", "https://api.duffel.com/air/airlines"),
        ("GET", "https://api.duffel.com/places/suggestions?query=London"),
        ("POST", "https://api.duffel.com/air/offer_requests"),
        ("GET", "https://api.duffel.com/air/offers/off_1"),
        ("GET", "https://api.duffel.com/air/seat_maps?offer_id=off_1"),
        ("POST", "https://api.duffel.com/air/orders"),
        ("GET", "https://api.duffel.com/air/orders/ord_1"),
        ("POST", "https://api.duffel.com/air/order_cancellations"),
    ]
    assert {r.get_header("Duffel-version") for r in captured} == {"v2"}


def test_live_preview_search_flights_without_token():
    os.environ.pop("OAUTH_REQUIRED", None)
    DIContainer.reset()

    @mcp_app(module=FlightStudioModule, server=ServerConfig(name="flight-preview"))
    class PreviewApp:
        pass

    app = asyncio.run(McpApplicationFactory.create(PreviewApp))
    http_app = build_http_app(app, enable_cors=True, stateless=True)
    with TestClient(http_app) as client:
        called = client.post(
            "/widgets/preview/call",
            json={
                "tool": "search_flights",
                "arguments": {"origin": "JFK", "destination": "LAX", "departureDate": "2026-09-15"},
            },
        )
        assert called.status_code == 200, called.text
        body = called.json()
        assert body["structuredContent"]["offers"][0]["id"] == "off_mock123456"
        assert "JFK" in body["html"]
        assert "flight-search-results" in body["resourceUri"]
