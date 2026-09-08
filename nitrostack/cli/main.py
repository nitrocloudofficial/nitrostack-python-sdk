import os
import re
import sys
import argparse
import shutil
import subprocess
import time
from pathlib import Path

from nitrostack.cli.generate import generate_component, generate_module as generate_module_from_template
from nitrostack.cli.install import install_dependencies
from nitrostack.cli.pack import pack_project
from nitrostack.cli.skills import run_skills_flow
from nitrostack.cli.upgrade import upgrade_project
from nitrostack.cli.validators import format_report, validate_project

MAIN_TEMPLATE = """import asyncio
from nitrostack import McpApplicationFactory
from app_module import AppModule

async def main():
    app = await McpApplicationFactory.create(AppModule)
    await app.start()

if __name__ == "__main__":
    asyncio.run(main())
"""

APP_MODULE_TEMPLATE = """from nitrostack import module
from modules.calculator.calculator_module import CalculatorModule

@module(
    name="app",
    imports=[CalculatorModule],
    controllers=[],
    providers=[],
    exports=[]
)
class AppModule:
    pass
"""

CALC_MODULE_TEMPLATE = """from nitrostack import module
from modules.calculator.calculator_tools import CalculatorController
from modules.calculator.calculator_service import CalculatorService

@module(
    name="calculator",
    imports=[],
    controllers=[CalculatorController],
    providers=[CalculatorService],
    exports=[CalculatorService]
)
class CalculatorModule:
    pass
"""

CALC_SERVICE_TEMPLATE = """from nitrostack import injectable

@injectable(deps=[])
class CalculatorService:
    def add(self, a: float, b: float) -> float:
        return a + b
"""

CALC_TOOLS_TEMPLATE = """from nitrostack import injectable, tool, ExecutionContext
from modules.calculator.calculator_service import CalculatorService
from pydantic import BaseModel

class AddInput(BaseModel):
    a: float
    b: float

@injectable(deps=[CalculatorService])
class CalculatorController:
    def __init__(self, service: CalculatorService):
        self.service = service

    @tool(
        name="add",
        description="Add two numbers together",
        input_schema=AddInput
    )
    async def add(self, input: AddInput, context: ExecutionContext) -> float:
        context.logger.info(f"Adding {input.a} and {input.b}")
        return self.service.add(input.a, input.b)
"""

FOOD_APP_MODULE_TEMPLATE = """from nitrostack import module
from modules.food_delivery.food_delivery_module import FoodDeliveryModule

@module(
    name="app",
    imports=[FoodDeliveryModule],
    controllers=[],
    providers=[],
    exports=[]
)
class AppModule:
    pass
"""

FOOD_DELIVERY_MODULE_TEMPLATE = """from nitrostack import module
from modules.food_delivery.food_delivery_tools import FoodDeliveryController
from modules.food_delivery.food_delivery_service import FoodDeliveryService

@module(
    name="food_delivery",
    imports=[],
    controllers=[FoodDeliveryController],
    providers=[FoodDeliveryService],
    exports=[FoodDeliveryService]
)
class FoodDeliveryModule:
    pass
"""

FOOD_DELIVERY_SERVICE_TEMPLATE = """from nitrostack import injectable

@injectable(deps=[])
class FoodDeliveryService:
    def __init__(self):
        # In-memory database of items and orders
        self.menu = {
            "pizza": {"price": 12.99, "prep_time": 15},
            "burger": {"price": 8.99, "prep_time": 10},
            "salad": {"price": 7.49, "prep_time": 5},
            "sushi": {"price": 15.99, "prep_time": 20}
        }
        self.orders = {}
        self.order_counter = 1000

    def get_menu(self):
        return self.menu

    def place_order(self, item: str, quantity: int) -> dict:
        item_lower = item.lower()
        if item_lower not in self.menu:
            return {"status": "error", "message": f"Item '{item}' not found in the menu."}
        
        self.order_counter += 1
        order_id = f"ORDER-{self.order_counter}"
        
        price = self.menu[item_lower]["price"] * quantity
        prep_time = self.menu[item_lower]["prep_time"]
        
        self.orders[order_id] = {
            "order_id": order_id,
            "item": item_lower,
            "quantity": quantity,
            "total_price": round(price, 2),
            "status": "Preparing",
            "time_remaining": prep_time
        }
        return self.orders[order_id]

    def get_order_status(self, order_id: str) -> dict:
        return self.orders.get(order_id, {"status": "error", "message": f"Order {order_id} not found."})
"""

FOOD_DELIVERY_TOOLS_TEMPLATE = """from nitrostack import injectable, tool, ExecutionContext
from modules.food_delivery.food_delivery_service import FoodDeliveryService
from pydantic import BaseModel, Field

class ViewMenuInput(BaseModel):
    pass

class PlaceOrderInput(BaseModel):
    item: str = Field(description="The food item to order (e.g., pizza, burger, salad, sushi)")
    quantity: int = Field(default=1, description="Number of items to order")

class OrderStatusInput(BaseModel):
    order_id: str = Field(description="The ID of the order to track (e.g., ORDER-1001)")

@injectable(deps=[FoodDeliveryService])
class FoodDeliveryController:
    def __init__(self, service: FoodDeliveryService):
        self.service = service

    @tool(
        name="view_menu",
        description="View the menu and list available food items and prices",
        input_schema=ViewMenuInput
    )
    async def view_menu(self, input: ViewMenuInput, context: ExecutionContext) -> dict:
        context.logger.info("Fetching food delivery menu...")
        return {"menu": self.service.get_menu()}

    @tool(
        name="place_order",
        description="Place a food delivery order for a menu item",
        input_schema=PlaceOrderInput
    )
    async def place_order(self, input: PlaceOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Placing order for {input.quantity}x {input.item}")
        return self.service.place_order(input.item, input.quantity)

    @tool(
        name="track_order",
        description="Track the status of an existing food delivery order",
        input_schema=OrderStatusInput
    )
    async def track_order(self, input: OrderStatusInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Tracking order status for {input.order_id}")
        return self.service.get_order_status(input.order_id)
"""

FLIGHT_APP_MODULE_TEMPLATE = """from nitrostack import module, ConfigModule, OAuthModule
from modules.flights.flights_module import FlightsModule
from health.system_health import SystemHealthCheck
import os

@module(
    name="app",
    imports=[
        ConfigModule.for_root(
            env_file_path=".env",
            defaults={"RESOURCE_URI": "https://mcplocal", "PORT": "3000"}
        ),
        # Configure OAuth resource protection
        OAuthModule.for_root(
            resource_uri=os.environ.get("RESOURCE_URI", "https://mcplocal"),
            authorization_servers=[os.environ.get("AUTH_SERVER_URL", "https://dev-5dt0utuk315713tjm.us.auth0.com")],
            scopes_supported=["read", "write", "admin"],
            token_introspection_endpoint=os.environ.get("INTROSPECTION_ENDPOINT"),
            token_introspection_client_id=os.environ.get("INTROSPECTION_CLIENT_ID"),
            token_introspection_client_secret=os.environ.get("INTROSPECTION_CLIENT_SECRET"),
            audience=os.environ.get("TOKEN_AUDIENCE"),
            issuer=os.environ.get("TOKEN_ISSUER")
        ),
        FlightsModule
    ],
    controllers=[],
    providers=[SystemHealthCheck],
    exports=[]
)
class AppModule:
    pass
"""

FLIGHT_OAUTH_GUARD_TEMPLATE = """from nitrostack import ExecutionContext

def create_scope_guard(required_scopes: list):
    class ScopeGuard:
        async def can_activate(self, context: ExecutionContext) -> bool:
            user_scopes = getattr(context.auth, "scopes", [])
            missing_scopes = [s for s in required_scopes if s not in user_scopes]
            if missing_scopes:
                raise ValueError(
                    f"Insufficient scope. Required: {', '.join(required_scopes)}. "
                    f"Missing: {', '.join(missing_scopes)}"
                )
            return True
    return ScopeGuard
"""

FLIGHT_SYSTEM_HEALTH_TEMPLATE = """import time
from nitrostack import health_check

class SystemHealthCheck:
    def __init__(self):
        self.start_time = time.time()

    @health_check("system")
    def check_system(self) -> bool:
        uptime = time.time() - self.start_time
        return uptime >= 0
"""

FLIGHT_DUFFEL_SERVICE_TEMPLATE = """import os
import sys
import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional
from nitrostack import injectable

@injectable()
class DuffelService:
    def __init__(self):
        self.api_key = os.environ.get("DUFFEL_API_KEY")
        self.is_mock = not self.api_key or self.api_key.startswith("your-") or len(self.api_key) < 5

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.is_mock:
            raise ValueError("Duffel API key is not configured. Running in mock mode.")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": "v2",
            "Content-Type": "application/json"
        }
        url = f"https://api.duffel.com{path}"
        req_data = json.dumps({"data": data}).encode("utf-8") if data else None
        
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res_payload = json.loads(response.read().decode("utf-8"))
                return res_payload.get("data", {})
        except Exception as e:
            sys.stderr.write(f"Duffel API Request failed: {e}\\n")
            sys.stderr.flush()
            raise e

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "orq_mock123456",
                "offers": [
                    {
                        "id": "off_mock123456",
                        "total_amount": "450.00",
                        "total_currency": "USD",
                        "expires_at": "2026-12-31T12:00:00Z",
                        "slices": [
                            {
                                "origin": {"iata_code": params["origin"], "name": "Origin Airport", "city_name": "Origin City"},
                                "destination": {"iata_code": params["destination"], "name": "Dest Airport", "city_name": "Dest City"},
                                "duration": "PT6H30M",
                                "segments": [
                                    {
                                        "id": "seg_outbound",
                                        "origin": {"iata_code": params["origin"]},
                                        "destination": {"iata_code": params["destination"]},
                                        "departing_at": f"{params['departureDate']}T08:00:00Z",
                                        "arriving_at": f"{params['departureDate']}T14:30:00Z",
                                        "marketing_carrier": {"name": "Mock Airlines"},
                                        "marketing_carrier_flight_number": "MK123",
                                        "aircraft": {"name": "Boeing 787"}
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        
        slices = [
            {
                "origin": params["origin"],
                "destination": params["destination"],
                "departure_date": params["departureDate"]
            }
        ]
        if params.get("returnDate"):
            slices.append({
                "origin": params["destination"],
                "destination": params["origin"],
                "departure_date": params["returnDate"]
            })
            
        duffel_params = {
            "slices": slices,
            "passengers": params.get("passengers", [{"type": "adult"}]),
            "cabin_class": params.get("cabinClass", "economy"),
            "return_offers": True
        }
        res = self._request("POST", "/air/offer_requests", duffel_params)
        return {
            "id": res.get("id"),
            "offers": res.get("offers", []),
            "passengers": res.get("passengers", []),
            "slices": res.get("slices", [])
        }

    async def get_offer(self, offer_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": offer_id,
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "slices": [
                    {
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/air/offers/{offer_id}")

    async def get_seats_for_offer(self, offer_id: str) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {
                    "cabin_class": "economy",
                    "rows": [
                        {
                            "row_number": 10,
                            "sections": [
                                {
                                    "elements": [
                                        {
                                            "type": "seat",
                                            "id": "seat_10a",
                                            "designator": "10A",
                                            "available_services": [{"total_amount": "25.00", "total_currency": "USD"}],
                                            "disclosures": ["window"]
                                        },
                                        {
                                            "type": "seat",
                                            "id": "seat_10b",
                                            "designator": "10B",
                                            "available_services": [{"total_amount": "0.00", "total_currency": "USD"}],
                                            "disclosures": ["middle"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        res = self._request("GET", f"/air/seat_maps?offer_id={offer_id}")
        return res if isinstance(res, list) else []

    async def create_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ord_mock123456",
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "booking_reference": "ABCXYZ",
                "passengers": [
                    {
                        "id": f"pax_{idx}",
                        "given_name": p["given_name"],
                        "family_name": p["family_name"],
                        "type": "adult"
                    } for idx, p in enumerate(params["passengers"])
                ],
                "slices": [
                    {
                        "origin": {"iata_code": "JFK"},
                        "destination": {"iata_code": "LAX"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z"
                            }
                        ]
                    }
                ]
            }
        
        order_payload = {
            "selected_offers": params["selectedOffers"],
            "passengers": params["passengers"],
            "type": "hold"
        }
        return self._request("POST", "/air/orders", order_payload)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": order_id,
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "booking_reference": "ABCXYZ",
                "created_at": "2026-06-25T12:00:00Z",
                "expires_at": "2026-12-31T12:00:00Z",
                "passengers": [
                    {
                        "id": "pax_0",
                        "given_name": "John",
                        "family_name": "Doe",
                        "type": "adult",
                        "email": "john@example.com",
                        "phone_number": "+1234567890"
                    }
                ],
                "slices": [
                    {
                        "id": "sli_mock",
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/air/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ocr_mock123456",
                "refund_amount": "450.00",
                "refund_currency": "USD",
                "confirmed_at": "2026-06-25T12:30:00Z"
            }
        cancel_payload = {"order_id": order_id}
        return self._request("POST", "/air/order_cancellations", cancel_payload)

    async def get_airlines(self) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {"iata_code": "AA", "name": "American Airlines"},
                {"iata_code": "DL", "name": "Delta Air Lines"},
                {"iata_code": "UA", "name": "United Airlines"},
                {"iata_code": "BA", "name": "British Airways"}
            ]
        res = self._request("GET", "/air/airlines")
        return res if isinstance(res, list) else []
"""

FLIGHT_MODULE_TEMPLATE = """from nitrostack import module
from modules.flights.flights_tools import FlightTools
from modules.flights.booking_tools import BookingTools
from modules.flights.flights_prompts import FlightPrompts
from modules.flights.flights_resources import FlightResources
from services.duffel_service import DuffelService

@module(
    name="flights",
    controllers=[FlightTools, BookingTools, FlightPrompts, FlightResources],
    providers=[DuffelService],
    exports=[DuffelService]
)
class FlightsModule:
    pass
"""

FLIGHT_TOOLS_TEMPLATE = """from nitrostack import injectable, tool, use_guards, OAuthGuard, ExecutionContext
from services.duffel_service import DuffelService
from guards.oauth_guard import create_scope_guard
from pydantic import BaseModel, Field
from typing import Optional

class SearchFlightsInput(BaseModel):
    origin: str = Field(description="Origin airport IATA code (e.g., 'JFK', 'LHR')")
    destination: str = Field(description="Destination airport IATA code (e.g., 'LAX', 'CDG')")
    departureDate: str = Field(description="Departure date in YYYY-MM-DD format")
    returnDate: Optional[str] = Field(default=None, description="Return date in YYYY-MM-DD format for round trip")
    adults: int = Field(default=1, description="Number of adult passengers (18+)")
    cabinClass: str = Field(default="economy", description="Preferred cabin class (economy, premium_economy, business, first)")

class FlightDetailsInput(BaseModel):
    offerId: str = Field(description="The flight offer ID from search results")

class AirportSearchInput(BaseModel):
    query: str = Field(description="The search query for airports (e.g., 'London', 'New York')")

class GetAirlinesInput(BaseModel):
    pass

@injectable(deps=[DuffelService])
class FlightTools:
    def __init__(self, service: DuffelService):
        self.service = service

    @tool(
        name="search_flights",
        title="Search Flights",
        description="Search for flight offers based on origin, destination, dates, and preferences.",
        input_schema=SearchFlightsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def search_flights(self, input: SearchFlightsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Searching flights from {input.origin} to {input.destination}")
        res = await self.service.search_flights(input.model_dump())
        return res

    @tool(
        name="get_flight_details",
        title="Get Flight Details",
        description="Get detailed information about a specific flight offer including baggage allowance, conditions.",
        input_schema=FlightDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def get_flight_details(self, input: FlightDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching flight details for offer {input.offerId}")
        res = await self.service.get_offer(input.offerId)
        return res

    @tool(
        name="search_airports",
        title="Search Airports",
        description="Search for airports by query string.",
        input_schema=AirportSearchInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def search_airports(self, input: AirportSearchInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Searching airports for query: {input.query}")
        return {"airports": [{"iataCode": "JFK", "name": "John F. Kennedy Airport"}]}

    @tool(
        name="get_airlines",
        title="Get Airlines",
        description="Get list of common airlines.",
        input_schema=GetAirlinesInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def get_airlines(self, input: GetAirlinesInput, context: ExecutionContext) -> dict:
        context.logger.info("Fetching common airlines")
        res = await self.service.get_airlines()
        return {"airlines": res}
"""

FLIGHT_BOOKING_TOOLS_TEMPLATE = """from nitrostack import injectable, tool, use_guards, OAuthGuard, ExecutionContext
from services.duffel_service import DuffelService
from guards.oauth_guard import create_scope_guard
from pydantic import BaseModel, Field
import json

class CreateOrderInput(BaseModel):
    offerId: str = Field(description="The offer ID to book")
    passengers: str = Field(description="JSON string containing array of passenger objects. Each passenger must have: title (mr/ms/mrs/miss/dr), givenName (first name), familyName (last name), gender (M/F), bornOn (YYYY-MM-DD), email, phoneNumber.")

class OrderDetailsInput(BaseModel):
    orderId: str = Field(description="The order ID")

class SeatMapInput(BaseModel):
    offerId: str = Field(description="The offer ID to get seats for")

class CancelOrderInput(BaseModel):
    orderId: str = Field(description="The order ID to cancel")

@injectable(deps=[DuffelService])
class BookingTools:
    def __init__(self, service: DuffelService):
        self.service = service

    @tool(
        name="create_order",
        title="Create Order",
        description="Create a flight order with hold (no payment required). Collect passenger details first.",
        input_schema=CreateOrderInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["write"]))
    async def create_order(self, input: CreateOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Creating flight order for offer {input.offerId}")
        try:
            passengers_array = json.loads(input.passengers)
        except Exception:
            raise ValueError("Invalid passengers JSON format")
            
        passengers = []
        for p in passengers_array:
            passengers.append({
                "title": p.get("title", "mr"),
                "given_name": p.get("givenName"),
                "family_name": p.get("familyName"),
                "gender": p.get("gender", "M"),
                "born_on": p.get("bornOn"),
                "email": p.get("email"),
                "phone_number": p.get("phoneNumber")
            })
            
        order_params = {
            "selectedOffers": [input.offerId],
            "passengers": passengers
        }
        res = await self.service.create_order(order_params)
        return res

    @tool(
        name="get_order_details",
        title="Get Order Details",
        description="Get detailed information about an order.",
        input_schema=OrderDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def get_order_details(self, input: OrderDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching details for order {input.orderId}")
        res = await self.service.get_order(input.orderId)
        return res

    @tool(
        name="get_seat_map",
        title="Get Seat Map",
        description="Get available seats for a flight offer to allow seat selection.",
        input_schema=SeatMapInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def get_seat_map(self, input: SeatMapInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching seat map for offer {input.offerId}")
        res = await self.service.get_seats_for_offer(input.offerId)
        return {"offerId": input.offerId, "cabins": res}

    @tool(
        name="cancel_order",
        title="Cancel Order",
        description="Cancel a flight order and request refund if applicable.",
        input_schema=CancelOrderInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["write"]))
    async def cancel_order(self, input: CancelOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Cancelling order {input.orderId}")
        res = await self.service.cancel_order(input.orderId)
        return res
"""

FLIGHT_PROMPTS_TEMPLATE = """from nitrostack import injectable, prompt, ExecutionContext
from services.duffel_service import DuffelService
from typing import List

@injectable(deps=[DuffelService])
class FlightPrompts:
    def __init__(self, service: DuffelService):
        self.service = service

    @prompt(
        name="flight_search_assistant",
        description="An AI assistant specialized in helping users search for flights and make booking decisions."
    )
    async def flight_search_assistant(self, args: dict, context: ExecutionContext) -> str:
        return "You are a professional flight booking assistant. Help the user search for flights using search_flights, search_airports, and assist in booking holds."

    @prompt(
        name="flight_comparison",
        description="Compare multiple flight offers and provide recommendations."
    )
    async def flight_comparison(self, args: dict, context: ExecutionContext) -> str:
        return "Compare the provided flight offer details, check layover times, durations, and pricing, and give the user a summary of best options."
"""

FLIGHT_RESOURCES_TEMPLATE = """from nitrostack import injectable, resource, ExecutionContext
from services.duffel_service import DuffelService

@injectable(deps=[DuffelService])
class FlightResources:
    def __init__(self, service: DuffelService):
        self.service = service

    @resource(
        uri="flight://popular-routes",
        name="Popular Flight Routes",
        description="Information about popular routes and pricing",
        mime_type="application/json"
    )
    async def popular_routes(self, context: ExecutionContext) -> dict:
        return {
            "routes": [
                {"route": "JFK -> LAX", "price": "$200-400"},
                {"route": "LHR -> JFK", "price": "$400-800"}
            ]
        }

    @resource(
        uri="flight://booking-guide",
        name="Flight Booking Guide",
        description="Guide on searching and booking flights",
        mime_type="text/markdown"
    )
    async def booking_guide(self, context: ExecutionContext) -> str:
        return "# Flight Booking Guide\\n\\n1. Search flights.\\n2. Collect passenger info.\\n3. Create order hold."
"""

OAUTH_SETUP_TEMPLATE = """# OAuth 2.1 Server Setup Guide

To run your flight booking MCP server with OAuth 2.1 protection, you need to configure an OAuth authorization server (like Keycloak, Auth0, Hydra, or a local mock OAuth server).

## 1. Local Configuration

Add the following environment variables to your `.env` file to configure resource protection:

```env
# --- Enforcement gate -------------------------------------------------------
# Unset / false (default): tokens are NOT enforced. Studio and Inspector can
#   call tools without authenticating, against mock data. Best for local dev.
# true: Bearer tokens are enforced. If no verifier (JWKS_URI or an
#   introspection endpoint) is configured, the server still starts but rejects
#   every protected request -- fail closed, never fail open.
OAUTH_REQUIRED=true

# --- Server identity --------------------------------------------------------
RESOURCE_URI=http://localhost:3000/mcp
AUTH_SERVER_URL=https://your-tenant.us.auth0.com

# --- Token verification: pick ONE of the two ---------------------------------
# 1) JWKS -- verifies signatures locally, no network call per request.
JWKS_URI=https://your-tenant.us.auth0.com/.well-known/jwks.json

# 2) Or RFC 7662 introspection -- asks the authorization server per token.
#    Both spellings are accepted; OAUTH_INTROSPECTION_ENDPOINT wins if both set.
# OAUTH_INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
# INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
# INTROSPECTION_CLIENT_ID=your-introspection-client-id
# INTROSPECTION_CLIENT_SECRET=your-introspection-client-secret

# --- Token claim validation --------------------------------------------------
# Audience must match, or the token is rejected (RFC 8707). Defaults to RESOURCE_URI.
TOKEN_AUDIENCE=http://localhost:3000/mcp
TOKEN_ISSUER=https://your-tenant.us.auth0.com/

# --- Dynamic Client Registration (RFC 7591, optional, off by default) --------
# Serves only the statically configured client below. Requires BOTH the flag
# and OAUTH_CLIENT_ID -- without a client id it stays disabled.
# OAUTH_ENABLE_CLIENT_REGISTRATION=true
# OAUTH_CLIENT_ID=your-client-id
# OAUTH_CLIENT_SECRET=your-client-secret

# --- Tuning ------------------------------------------------------------------
# Seconds to cache a successful introspection result (default 300; 0 disables).
# OAUTH_TOKEN_CACHE_SECONDS=300
# Port for the .well-known discovery server (default 3005).
# OAUTH_DISCOVERY_PORT=3005
```

## 2. Protected Routes

The tools in this server use the `@use_guards(OAuthGuard, create_scope_guard([...]))` decorators to automatically protect endpoints:
* **Public**: No guards (or custom public filters).
* **Read-Protected**: Requires valid access token with `read` scope.
* **Write-Protected**: Requires valid access token with `write` scope.

When calling protected tools, the client must pass a valid Bearer token in the `Authorization` header.
"""

ENV_TEMPLATE = """PORT=3000
NODE_ENV=development
"""

DEFAULT_PROJECT_NAME = "my-mcp-server"
DEFAULT_MCP_PORT = "3000"
DEFAULT_WIDGETS_PORT = "3001"

# Official CLI names → on-disk template directories.
OFFICIAL_TEMPLATES = {
    "python-starter": "starter",
    "python-pizzaz": "pizzaz",
    "python-oauth": "flight-booking",
}

REQUIREMENTS_TEMPLATE = """nitrostack
"""

TOOL_TEMPLATE = """from nitrostack import tool, widget, ExecutionContext
from pydantic import BaseModel

class {camel_name}Input(BaseModel):
    # Add input parameters here
    pass

@tool(
    name="{name}",
    description="Implement your tool description here",
    input_schema={camel_name}Input
)
@widget("{name}")
async def {name}_handler(input: {camel_name}Input, context: ExecutionContext):
    context.logger.info("Executing tool {name}")
    return {{"status": "success"}}
"""

WIDGET_PREVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Widget preview</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 16px; }
    iframe { width: 100%; height: 380px; border: 1px solid #ddd; border-radius: 8px; }
    button { margin: 0 8px 12px 0; }
    textarea { width: 100%; height: 110px; font-family: ui-monospace, monospace; }
    .note { color: #475569; font-size: 0.9rem; max-width: 820px; line-height: 1.45; }
  </style>
</head>
<body>
  <p class="note">
    This static file does <strong>not</strong> follow MCP Inspector tool calls.
    Inspector JSON is the live result (e.g. all matching pizza shops). Open
    <code>http://localhost:3000/widgets/preview</code> with the server running to
    render that same output, or paste <code>structuredContent</code> below and Inject.
  </p>
  <div id="buttons"></div>
  <textarea id="json">__DEFAULT_JSON__</textarea>
  <p><button id="inject">Inject JSON into iframe</button></p>
  <iframe id="frame" src="out/__FIRST__.html"></iframe>
  <script>
    const routes = __ROUTES__;
    const buttons = document.getElementById("buttons");
    const frame = document.getElementById("frame");
    routes.forEach((route) => {
      const b = document.createElement("button");
      b.textContent = route;
      b.onclick = () => { frame.src = "out/" + route + ".html"; };
      buttons.appendChild(b);
    });
    function inject() {
      let data = {};
      try { data = JSON.parse(document.getElementById("json").value); } catch (e) { alert("Invalid JSON"); return; }
      const win = frame.contentWindow;
      if (!win) return;
      const payload = { structuredContent: data };
      try {
        win.openai = Object.assign(win.openai || {}, { toolOutput: payload });
        win.dispatchEvent(new CustomEvent("openai:set_globals", { detail: { globals: { toolOutput: payload } } }));
      } catch (e) {}
      win.postMessage({ type: "setGlobals", globals: { toolOutput: payload } }, "*");
      win.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: payload }, "*");
    }
    document.getElementById("inject").onclick = inject;
    frame.addEventListener("load", () => setTimeout(inject, 80));
  </script>
</body>
</html>
"""

SAMPLE_PIZZA_PREVIEW_JSON = """{
  "shops": [
    {
      "id": "tonys-pizza",
      "name": "Tony's New York Pizza",
      "address": "123 Main St, San Francisco, CA 94102",
      "rating": 4.5,
      "priceLevel": 2,
      "openNow": true,
      "image": "https://images.unsplash.com/photo-1513104890138-7c749659a591"
    }
  ],
  "totalShops": 1
}"""


_GENERATE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WIDGET_ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_generate_name(name: str) -> str:
    """Reject path segments so ``generate tool ../../ESCAPED`` cannot write outside cwd."""
    name = (name or "").strip()
    if not name or os.path.basename(name) != name or ".." in name or not _GENERATE_NAME_RE.fullmatch(name):
        raise ValueError(
            "name must be a Python identifier (letters, digits, underscore) "
            "and cannot contain path separators"
        )
    return name


def _sanitize_widget_route(route: str) -> str:
    route = (route or "").strip()
    if not route or os.path.basename(route) != route or ".." in route or not _WIDGET_ROUTE_RE.fullmatch(route):
        raise ValueError("widget route must be a single alphanumeric path segment")
    return route


def _assert_dest_inside_root(dest: str, root: str) -> str:
    dest_abs = os.path.realpath(dest)
    root_abs = os.path.realpath(root)
    try:
        common = os.path.commonpath([dest_abs, root_abs])
    except ValueError as exc:
        raise ValueError("generated path would escape the project directory") from exc
    if common != root_abs:
        raise ValueError("generated path would escape the project directory")
    return dest_abs


def write_widget_html(project_dir: str, route: str, *, overwrite: bool = False) -> str:
    """Write ``widgets/out/{route}.html`` if missing (Python-only static widget)."""
    from nitrostack.widgets.route_templates import build_widget_html_for_route

    route = _sanitize_widget_route(route)
    project_dir = os.path.abspath(project_dir or ".")
    out_dir = os.path.join(project_dir, "widgets", "out")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{route}.html")
    _assert_dest_inside_root(dest, project_dir)
    if overwrite or not os.path.exists(dest):
        with open(dest, "w", encoding="utf-8") as f:
            f.write(build_widget_html_for_route(route))
    return dest


def write_widget_preview(project_dir: str) -> None:
    from html import escape as html_escape

    from nitrostack.widgets.html_util import json_for_inline_script

    out_dir = os.path.join(project_dir, "widgets", "out")
    if not os.path.isdir(out_dir):
        return
    routes = sorted(p[:-5] for p in os.listdir(out_dir) if p.endswith(".html"))
    if not routes:
        return
    first = "pizza-list" if "pizza-list" in routes else routes[0]
    default_payload: dict = {"status": "success"}
    if "pizza-list" in routes or "pizza-map" in routes:
        import json as json_lib

        default_payload = json_lib.loads(SAMPLE_PIZZA_PREVIEW_JSON)
    html = (
        WIDGET_PREVIEW_HTML.replace("__FIRST__", html_escape(first, quote=True))
        .replace("__ROUTES__", json_for_inline_script(routes))
        .replace("__DEFAULT_JSON__", html_escape(json_for_inline_script(default_payload), quote=False))
    )
    dest = os.path.join(project_dir, "widgets", "preview.html")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)


def ensure_python_widgets(project_dir: str) -> list:
    """Create missing ``widgets/out/{route}.html`` for every ``@widget`` in the project."""
    routes = []
    for root, _dirs, files in os.walk(project_dir):
        parts = set(root.split(os.sep))
        if "node_modules" in parts or ".venv" in parts:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            routes.extend(re.findall(r'@widget\(\s*["\']([^"\']+)["\']', text))
            routes.extend(re.findall(r'WidgetOptions\(\s*route\s*=\s*["\']([^"\']+)["\']', text))
    # Only routes that were actually scaffolded go into the returned list — callers
    # print it as "created", so appending before the write would report a widget that
    # never landed on disk. A rejected route is surfaced rather than skipped silently.
    unique = []
    seen = set()
    for route in routes:
        if route in seen:
            continue
        seen.add(route)
        try:
            write_widget_html(project_dir, route, overwrite=False)
        except ValueError as exc:
            print(f"Warning: skipped widget route {route!r}: {exc}")
            continue
        unique.append(route)
    write_widget_preview(project_dir)
    return unique

MODULE_TEMPLATE = """from nitrostack import module

@module(
    name="{name}",
    imports=[],
    controllers=[],
    providers=[],
    exports=[]
)
class {camel_name}Module:
    pass
"""

def print_banner():
    banner = """\033[34m╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   _   _  ___ _____ ____   ___                            ║
║  | \\ | ||_ _|_   _|  _ \\ / _ \\                           ║
║  |  \\| | | |  | | | |_) | | | |                          ║
║  | |\\  | | |  | | |  _ <| |_| |                          ║
║  |_| \\_||___| |_| |_| \\_\\\\___/                           ║
║                                                          ║
║   \033[1;34mNITROSTACK\033[0;34m — Official MCP Framework                   ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝\033[0m"""
    print(banner)

def _prompt(message: str, default: str = "") -> str:
    default_hint = f" [{default}]" if default else ""
    sys.stdout.write(f"\033[32m? \033[1;37m{message}:\033[0m{default_hint} ")
    sys.stdout.flush()
    try:
        value = sys.stdin.readline().strip()
    except Exception:
        value = ""
    return value or default


def _resolve_template(template: str) -> str:
    key = (template or "").strip().lower()
    if key not in OFFICIAL_TEMPLATES:
        names = ", ".join(OFFICIAL_TEMPLATES)
        print(f"Error: Unknown template '{template}'. Use one of: {names}")
        sys.exit(1)
    return key


def _prompt_template() -> str:
    print("\033[32m? \033[1;37mChoose a template:\033[0m")
    print("  \033[34m1. python-starter\033[0m     Starter — simple calculator for learning basics")
    print("  \033[34m2. python-pizzaz\033[0m      Advanced — pizza shop finder with maps & widgets")
    print("  \033[34m3. python-oauth\033[0m       Flight booking with OAuth 2.1 auth")
    by_choice = {
        "1": "python-starter",
        "2": "python-pizzaz",
        "3": "python-oauth",
    }
    while True:
        choice = _prompt("Enter choice (1-3) or template name", "1")
        if choice in by_choice:
            return by_choice[choice]
        if choice.lower() in OFFICIAL_TEMPLATES:
            return choice.lower()
        print("Please enter 1, 2, 3, or an explicit template name (python-starter, python-pizzaz, python-oauth).")


def _normalize_port(value, label):
    try:
        port = int(value)
    except (TypeError, ValueError):
        print(f"Error: Invalid {label} '{value}'.")
        sys.exit(1)
    if port < 1 or port > 65535:
        print(f"Error: {label} {port} is out of range (1-65535).")
        sys.exit(1)
    return str(port)


def _read_project_env():
    values = {}
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return values
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception:
        pass
    return values


def _resolve_runtime_ports(port=None, widget=None):
    """Resolve MCP and widget ports. Explicit --port/--widget flags override defaults."""
    project_env = _read_project_env()
    if port is not None:
        mcp_port = _normalize_port(port, "--port")
    else:
        mcp_port = (
            os.environ.get("PORT")
            or os.environ.get("MCP_SERVER_PORT")
            or project_env.get("PORT")
            or project_env.get("MCP_SERVER_PORT")
            or DEFAULT_MCP_PORT
        )
        mcp_port = str(mcp_port)
        if widget is None and mcp_port == DEFAULT_WIDGETS_PORT:
            mcp_port = DEFAULT_MCP_PORT

    if widget is not None:
        widgets_port = _normalize_port(widget, "--widget")
    else:
        widgets_port = os.environ.get("WIDGETS_DEV_PORT") or project_env.get("WIDGETS_DEV_PORT") or DEFAULT_WIDGETS_PORT
        widgets_port = str(widgets_port)
        if port is None and widgets_port == DEFAULT_MCP_PORT:
            widgets_port = DEFAULT_WIDGETS_PORT
    return mcp_port, widgets_port


def _upsert_env_var(lines, key, value):
    prefix = f"{key}="
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}\n")
    return new_lines


def _prompt_yes_no(message: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    sys.stdout.write(f"\033[32m? \033[1;37m{message}:\033[0m ({hint}) ")
    sys.stdout.flush()
    try:
        ans = sys.stdin.readline().strip().lower()
    except Exception:
        ans = ""
    if not ans:
        return default_yes
    if ans in ("y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return default_yes


def _find_npm():
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError(
            "npm was not found on PATH. Install Node.js (https://nodejs.org) and retry."
        )
    return npm


def _run_npm(args, **kwargs):
    """Run npm without shell=True. A list + shell=True is treated as `sh -c npm` and drops flags."""
    kwargs.pop("shell", None)
    return subprocess.run([_find_npm(), *args], **kwargs)


def _popen_npm(args, **kwargs):
    kwargs.pop("shell", None)
    return subprocess.Popen([_find_npm(), *args], **kwargs)


def _add_port_flags(parser):
    parser.add_argument(
        "--port",
        default=None,
        help=f"MCP HTTP port (default: {DEFAULT_MCP_PORT}; overrides PORT when set)",
    )
    parser.add_argument(
        "--widget",
        default=None,
        help=f"Widget dev server port (default: {DEFAULT_WIDGETS_PORT}; overrides WIDGETS_DEV_PORT when set)",
    )


def init_project(name: str = None, template: str = None, skip_install: bool = False, port=None, widget=None, force: bool = False):
    print_banner()

    # 1. Project name — optional CLI arg, otherwise the next readline
    if not name:
        name = _prompt("Project name", DEFAULT_PROJECT_NAME)
    name = (name or "").strip()
    if not name:
        print("Error: Project name cannot be empty.")
        sys.exit(1)

    # 2. Overwrite check
    if os.path.exists(name):
        sys.stdout.write(f"\033[32m? \033[1;37mDirectory '{name}' already exists. Overwrite?\033[0m (Yes/No) [No]: ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans not in ("y", "yes"):
            print("Initialization cancelled.")
            sys.exit(0)
        shutil.rmtree(name, ignore_errors=True)

    # 3. Select template (explicit python-* names)
    if not template:
        template = _prompt_template()
    else:
        template = _resolve_template(template)

    # 4. Description and Author
    description = _prompt("Description", "My awesome MCP server")
    author = _prompt("Author", "developer")

    # 5. Install dependencies (Y/n). --skip-install skips the prompt.
    if skip_install:
        install_deps = False
    else:
        install_deps = _prompt_yes_no("Install dependencies", default_yes=True)

    mcp_port = _normalize_port(port, "--port") if port is not None else DEFAULT_MCP_PORT
    widgets_port = _normalize_port(widget, "--widget") if widget is not None else DEFAULT_WIDGETS_PORT

    # 6. Copy template directory
    import nitrostack
    package_dir = os.path.dirname(nitrostack.__file__)
    template_dir = OFFICIAL_TEMPLATES[template]
    template_src_dir = os.path.join(package_dir, "templates", template_dir)

    if not os.path.exists(template_src_dir):
        print(f"Error: Template '{template}' not found at '{template_src_dir}'.")
        sys.exit(1)

    shutil.copytree(template_src_dir, name)
    widget_routes = ensure_python_widgets(name)
    print("\n\033[32m✓\033[0m Project created")
    if widget_routes:
        print(f"\033[32m✓\033[0m Python widgets: {', '.join(widget_routes)}")
        print("    HTML in widgets/out/ — preview: widgets/preview.html")

    # 7. Update .env file
    env_path = os.path.join(name, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith("SERVER_DESC="):
                new_lines.append(f'SERVER_DESC="{description}"\n')
            elif line.startswith("SERVER_AUTHOR="):
                new_lines.append(f'SERVER_AUTHOR="{author}"\n')
            else:
                new_lines.append(line)
        has_desc = any(line.startswith("SERVER_DESC=") for line in new_lines)
        has_author = any(line.startswith("SERVER_AUTHOR=") for line in new_lines)
        if not has_desc:
            new_lines.append(f'SERVER_DESC="{description}"\n')
        if not has_author:
            new_lines.append(f'SERVER_AUTHOR="{author}"\n')
        new_lines = _upsert_env_var(new_lines, "PORT", mcp_port)
        new_lines = _upsert_env_var(new_lines, "WIDGETS_DEV_PORT", widgets_port)
        new_lines = _upsert_env_var(new_lines, "NITROSTACK_APP_MODE", "universal")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    # 8. Update widgets package.json
    widgets_package_path = os.path.join(name, "src", "widgets", "package.json")
    if os.path.exists(widgets_package_path):
        import json
        try:
            with open(widgets_package_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            pkg["name"] = f"{name}-widgets"
            # The port is passed once by the CLI (`npm run dev -- --port N`), so it
            # is deliberately not baked into the script.
            scripts = pkg.setdefault("scripts", {})
            scripts["dev"] = "next dev"
            scripts["start"] = "next start"
            with open(widgets_package_path, "w", encoding="utf-8") as f:
                json.dump(pkg, f, indent=2)
        except Exception:
            pass

    # 9. Run npm install inside widgets directory
    widgets_dir = os.path.join(name, "src", "widgets")
    if os.path.exists(widgets_dir) and install_deps:
        print("Installing widget dependencies...")
        try:
            _run_npm(["--version"], capture_output=True, check=True, text=True)
            _run_npm(["install"], cwd=widgets_dir, check=True)
            print("\033[32m✓\033[0m Widget dependencies installed\n")
        except FileNotFoundError as e:
            print(f"Warning: {e}")
            print("Please run 'npm install' inside 'src/widgets' manually.\n")
        except subprocess.CalledProcessError as e:
            detail = (getattr(e, "stderr", None) or getattr(e, "stdout", None) or str(e)).strip()
            print(f"Warning: Failed to install widget dependencies: {detail}")
            print("Please run 'npm install' inside 'src/widgets' manually.\n")
    elif not install_deps:
        print("\033[32m✓\033[0m Skipped dependency install")

    run_skills_flow(os.path.abspath(name), force=force)

    # Success Card
    abs_path = os.path.abspath(name)
    success_box = f"""\033[36m╔══════════════════════════════════════════════════════════╗
║ \033[32m✓ Project Ready\033[36m                                          ║
║                                                          ║
║   Name: {name:<48} ║
║   Template: {template:<44} ║
║   Path: {abs_path:<48} ║
╚══════════════════════════════════════════════════════════╝\033[0m"""
    print(success_box)

    # Next Steps
    print("\n\033[1;37mNext steps:\033[0m")
    print(f" 1. \033[34mcd {name}\033[0m")
    if template == "python-oauth":
        print(" 2. Configure OAuth credentials in your \033[34m.env\033[0m file")
        print("    See \033[34mOAUTH_SETUP.md\033[0m for provider guides")
    else:
        print(" 2. Configure environment variables in \033[34m.env\033[0m")
    print(" 3. Start development server: \033[34mnitrostack-py dev\033[0m (or `python -m nitrostack.cli.main dev`)")
    print(" 4. Preview widgets: \033[34mhttp://localhost:3000/widgets/preview\033[0m (server running)")
    print("    Static file (manual JSON): \033[34mopen widgets/preview.html\033[0m")
    print(" 5. Inspector (HTTP): \033[34mMCP_TRANSPORT_TYPE=http MCP_STATELESS=true NITROSTACK_APP_MODE=universal python main.py\033[0m")
    print("    Connect Streamable HTTP to \033[34mhttp://localhost:3000/mcp\033[0m with \033[34mAuthentication = Off\033[0m")
    print("    Apps tab renders the widget from live \033[34mstructuredContent\033[0m (not widgets/preview.html)")
    print("    For open shops only set \033[34mopenNow=true\033[0m (list) or \033[34mfilter=open_now\033[0m (map)")
    print(" 6. NitroStudio cannot folder-connect a Python project (it looks for @nitrostack/core).")
    print("    Use Inspector HTTP as above, or Studio's custom MCP URL if it offers Streamable HTTP.")
    print("\nHappy coding! 🚀\n")

def run_dev(port=None, widget=None):
    target = "main.py"
    if not os.path.exists(target):
        print("Error: main.py not found in current directory.")
        sys.exit(1)

    mcp_port, widgets_port = _resolve_runtime_ports(port, widget)

    print(f"Starting hot-reload development server for {target}...")
    print(f"MCP server port: {mcp_port}")
    process = None
    widgets_process = None

    # Check if Next.js widgets are present
    widgets_dir = os.path.join(os.getcwd(), "src", "widgets")
    has_widgets = os.path.exists(widgets_dir) and os.path.exists(os.path.join(widgets_dir, "package.json"))
    if has_widgets:
        print(f"Starting widget development server on port {widgets_port}...")
        try:
            widgets_process = _popen_npm(
                ["run", "dev", "--", "--port", str(widgets_port)],
                cwd=widgets_dir,
            )
        except Exception as e:
            print(f"Warning: Could not start widget development server: {e}")

    def start_process():
        nonlocal process
        if process:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.abspath(".")
        env["PORT"] = mcp_port
        if has_widgets:
            env["WIDGETS_DEV_PORT"] = str(widgets_port)
        process = subprocess.Popen([sys.executable, target], env=env)

    def cleanup():
        nonlocal process, widgets_process
        if process:
            try:
                process.terminate()
            except Exception:
                pass
        if widgets_process:
            try:
                widgets_process.terminate()
            except Exception:
                pass

    try:
        start_process()
        
        watched_extensions = {".py", ".env"}
        
        def get_mtimes():
            mtimes = {}
            for root, dirs, files in os.walk("."):
                if any(part.startswith(".") or part in ("venv", "env", "__pycache__") for part in root.split(os.sep)):
                    continue
                for file in files:
                    ext = os.path.splitext(file)[1]
                    if ext in watched_extensions:
                        path = os.path.join(root, file)
                        try:
                            mtimes[path] = os.path.getmtime(path)
                        except Exception:
                            pass
            return mtimes

        # Try using watchfiles for high-performance, low-CPU file monitoring
        try:
            from watchfiles import watch
            print("Using watchfiles for high-performance file monitoring.")
            
            while True:
                for changes in watch("."):
                    should_restart = False
                    for change_type, path in changes:
                        ext = os.path.splitext(path)[1]
                        if ext in watched_extensions:
                            parts = os.path.normpath(path).split(os.sep)
                            if not any(p in parts for p in ("venv", "env", "__pycache__", ".git", ".pytest_cache", "nitrostack.egg-info")):
                                should_restart = True
                                break
                    if should_restart:
                        print("File changes detected! Restarting server...")
                        start_process()
                
                time.sleep(0.5)
                if process and process.poll() is not None:
                    print("Server process exited. Waiting for file changes to restart...")
                    
        except ImportError:
            print("watchfiles library not found. Falling back to standard polling...")
            last_mtimes = get_mtimes()
            try:
                while True:
                    time.sleep(1)
                    if process and process.poll() is not None:
                        print("Server process exited. Waiting for file changes to restart...")
                    current_mtimes = get_mtimes()
                    changed = False
                    if set(current_mtimes.keys()) != set(last_mtimes.keys()):
                        changed = True
                    else:
                        for path, mtime in current_mtimes.items():
                            if last_mtimes.get(path) != mtime:
                                changed = True
                                break
                    if changed:
                        print("File changes detected! Restarting server...")
                        start_process()
                        last_mtimes = current_mtimes
            except KeyboardInterrupt:
                pass
    except KeyboardInterrupt:
        print("\nStopping development server...")
        cleanup()

def run_start(port=None, widget=None):
    target = "main.py"
    if not os.path.exists(target):
        print("Error: main.py not found in current directory.")
        sys.exit(1)

    mcp_port, widgets_port = _resolve_runtime_ports(port, widget)

    print(f"Starting production server for {target}...")
    print(f"MCP HTTP: http://localhost:{mcp_port}/mcp")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(".")
    env["PORT"] = mcp_port
    env["WIDGETS_DEV_PORT"] = widgets_port
    env["NODE_ENV"] = "production"
    # Select HTTP explicitly rather than letting NODE_ENV=production fall through
    # to dual. Dual shuts HTTP down when STDIO reaches EOF, so `start` would exit
    # immediately wherever stdin is not held open (Docker without -i, systemd, CI).
    # `setdefault` keeps an explicit MCP_TRANSPORT_TYPE from the caller.
    env.setdefault("MCP_TRANSPORT_TYPE", "http")

    widgets_dir = os.path.join(os.getcwd(), "src", "widgets")
    widgets_process = None
    if os.path.exists(widgets_dir) and os.path.exists(os.path.join(widgets_dir, "package.json")):
        # `next start` needs a production build; Popen would succeed and the child
        # would fail with a bare "Could not find a production build", so check here.
        if not os.path.exists(os.path.join(widgets_dir, ".next")):
            print("Widgets are not built yet — skipping the widget server.")
            print("Run 'npm run build' inside 'src/widgets', then retry.\n")
        else:
            print(f"Starting widget server on port {widgets_port}...")
            try:
                widgets_process = _popen_npm(
                    ["run", "start", "--", "--port", str(widgets_port)],
                    cwd=widgets_dir,
                )
            except Exception as e:
                print(f"Warning: Could not start widget server: {e}")

    try:
        subprocess.run([sys.executable, target], env=env)
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        if widgets_process:
            try:
                widgets_process.terminate()
            except Exception:
                pass

def generate_tool(name: str):
    # Both validators run before anything is written. `_validate_generate_name` and
    # `_sanitize_widget_route` accept overlapping-but-different character sets (e.g.
    # `_foo` is a valid identifier but not a valid route; `my-tool` is the reverse),
    # so validating the route lazily inside `write_widget_html` would leave an orphan
    # `{name}_tool.py` behind whenever the two disagree — and that orphan then blocks
    # any retry with "File already exists".
    try:
        name = _validate_generate_name(name)
        _sanitize_widget_route(name)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    filename = f"{name}_tool.py"
    dest_py = os.path.abspath(filename)
    try:
        _assert_dest_inside_root(dest_py, os.getcwd())
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    if os.path.exists(filename):
        print(f"Error: File '{filename}' already exists.")
        sys.exit(1)
    camel_name = "".join(part.capitalize() for part in name.split("_"))
    content = TOOL_TEMPLATE.format(name=name, camel_name=camel_name)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        html_path = write_widget_html(".", name, overwrite=False)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    write_widget_preview(".")
    print(f"Generated tool boilerplate in '{filename}'")
    print(f"Generated widget HTML in '{html_path}'")

def generate_module(name: str):
    generate_module_from_template(name)

def get_claude_config_paths():
    paths = []
    home = os.path.expanduser("~")
    
    # Windows
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
        # Windows Store app path
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            store_dir = os.path.join(localappdata, "Packages")
            if os.path.exists(store_dir):
                try:
                    for folder in os.listdir(store_dir):
                        if folder.startswith("Claude_"):
                            paths.append(os.path.join(store_dir, folder, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
                except Exception:
                    pass
    # macOS
    elif sys.platform == "darwin":
        paths.append(os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json"))
    # Linux
    else:
        paths.append(os.path.join(home, ".config", "Claude", "claude_desktop_config.json"))
        
    return [p for p in paths if os.path.exists(os.path.dirname(p))]

def register_server(name: str, file_path: str):
    import json
    
    if not os.path.exists(file_path):
        print(f"Error: Script file '{file_path}' does not exist.")
        sys.exit(1)
        
    abs_file_path = os.path.abspath(file_path)
    python_exe = sys.executable
    
    config_paths = get_claude_config_paths()
    if not config_paths:
        print("Error: Could not find any Claude Desktop installation directories.")
        print("Please ensure Claude Desktop is installed on your machine.")
        sys.exit(1)
        
    registered_any = False
    for path in config_paths:
        try:
            config = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        try:
                            config = json.loads(content)
                        except json.JSONDecodeError:
                            print(f"Warning: Configuration file at '{path}' is not valid JSON. Resetting it.")
            
            if "mcpServers" not in config:
                config["mcpServers"] = {}
                
            config["mcpServers"][name] = {
                "command": python_exe,
                "args": [abs_file_path]
            }
            
            # Create directory if needed
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                
            print(f"Successfully registered server '{name}' in: {path}")
            registered_any = True
        except Exception as e:
            print(f"Error writing to config at '{path}': {e}")
            
    if registered_any:
        print("\nAll done! Please fully restart Claude Desktop to load your new server.")
    else:
        print("Error: Failed to register the server in any configuration files.")

def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="nitrostack-py",
        description="nitrostack-py CLI — Scaffold, develop, pack, and run NitroStack Python MCP servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nitrostack-py init my-server\n"
            "  nitrostack-py generate guard MyGuard\n"
            "  nitrostack-py pack --dry-run\n"
            "  nitrostack-py upgrade --dry-run\n"
            "  nitrostack-py install --production\n"
            "  nitrostack-py validate\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new NitroStack MCP server project")
    init_parser.add_argument("name", nargs="?", default=None, help="Name of the project directory (prompted if omitted)")
    init_parser.add_argument(
        "--template",
        choices=list(OFFICIAL_TEMPLATES.keys()),
        default=None,
        help="Template to use: python-starter, python-pizzaz, python-oauth (default: interactive prompt)",
    )
    init_parser.add_argument("--skip-install", action="store_true", help="Skip installing widget npm dependencies")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing agent skill directories when installing",
    )
    _add_port_flags(init_parser)

    # dev command
    dev_parser = subparsers.add_parser("dev", help="Start the hot-reloading development server")
    _add_port_flags(dev_parser)

    # start command
    start_parser = subparsers.add_parser("start", help="Start the production server")
    _add_port_flags(start_parser)

    # register command
    reg_parser = subparsers.add_parser("register", help="Register server script inside Claude Desktop configuration")
    reg_parser.add_argument("--name", default=os.path.basename(os.getcwd()), help="Name of the server (defaults to folder name)")
    reg_parser.add_argument("--file", default="main.py", help="Python script to register (defaults to main.py)")

    # generate command
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate boilerplate code (tool, module, guard, pipe, interceptor, filter, service)",
    )
    gen_subparsers = gen_parser.add_subparsers(dest="generator", metavar="type")

    tool_parser = gen_subparsers.add_parser("tool", help="Generate a new tool boilerplate")
    tool_parser.add_argument("name", help="Name of the tool")

    mod_parser = gen_subparsers.add_parser("module", help="Generate a new module boilerplate")
    mod_parser.add_argument("name", help="Name of the module")

    for kind, kind_help in (
        ("guard", "Generate an authorization guard"),
        ("pipe", "Generate a validation/transform pipe"),
        ("interceptor", "Generate an execution interceptor"),
        ("filter", "Generate an exception filter"),
        ("service", "Generate an injectable service"),
    ):
        kind_parser = gen_subparsers.add_parser(kind, help=kind_help)
        kind_parser.add_argument("name", help=f"Name of the {kind}")

    # pack command
    pack_parser = subparsers.add_parser(
        "pack",
        help="Build a deployable wheel of the current project (never includes .env/secrets)",
    )
    pack_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show files that would be packed without creating the artifact",
    )

    # upgrade command
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Update the nitrostack dependency to the latest (or a specific) PyPI version",
    )
    upgrade_parser.add_argument(
        "--version",
        dest="target_version",
        metavar="X.Y.Z",
        help="Pin nitrostack to this version instead of the latest PyPI release",
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the version change without modifying pyproject.toml",
    )
    upgrade_parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="Allow --version to pin an older release than the one currently declared",
    )

    # install command
    install_parser = subparsers.add_parser(
        "install",
        help="Install project dependencies from pyproject.toml / requirements.txt",
    )
    install_parser.add_argument(
        "--production",
        action="store_true",
        help="Skip development dependencies (optional extras and requirements-dev.txt)",
    )

    # validate command
    subparsers.add_parser(
        "validate",
        help="Lint project config, @mcp_app imports, and @module() class references",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    def _run_command(fn, *fn_args, **fn_kwargs):
        try:
            return fn(*fn_args, **fn_kwargs)
        except Exception as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    if args.command == "init":
        init_project(
            args.name,
            args.template,
            skip_install=args.skip_install,
            port=args.port,
            widget=args.widget,
            force=args.force,
        )
    elif args.command == "dev":
        run_dev(port=args.port, widget=args.widget)
    elif args.command == "start":
        run_start(port=args.port, widget=args.widget)
    elif args.command == "register":
        register_server(args.name, args.file)
    elif args.command == "generate":
        if not args.generator:
            gen_parser.print_help()
            sys.exit(1)
        if args.generator == "tool":
            generate_tool(args.name)
        elif args.generator == "module":
            generate_module(args.name)
        else:
            generate_component(args.generator, args.name)
    elif args.command == "pack":
        _run_command(pack_project, dry_run=args.dry_run)
    elif args.command == "upgrade":
        _run_command(
            upgrade_project,
            version=args.target_version,
            dry_run=args.dry_run,
            allow_downgrade=args.allow_downgrade,
        )
    elif args.command == "install":
        _run_command(install_dependencies, production=args.production)
    elif args.command == "validate":
        issues = _run_command(validate_project)
        print(format_report(issues))
        if any(issue.severity == "error" for issue in issues):
            sys.exit(1)

if __name__ == "__main__":
    main()
