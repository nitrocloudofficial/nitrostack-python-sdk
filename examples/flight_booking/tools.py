"""Flight-booking example tools."""

import json
from nitrostack import tool, injectable, ExecutionContext, use_guards, widget, OAuthGuard
from .models import SearchFlightsInput, FlightDetailsInput, AirportSearchInput, CreateOrderInput, OrderDetailsInput, SeatMapInput, CancelOrderInput, GetAirlinesInput
from .guards import create_scope_guard
from .service import DuffelService


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
    @widget("flight-search-results")
    async def search_flights(self, input: SearchFlightsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Searching flights from {input.origin} to {input.destination}")
        return await self.service.search_flights(input.model_dump())

    @tool(
        name="get_flight_details",
        title="Get Flight Details",
        description="Get detailed information about a specific flight offer including baggage allowance, conditions.",
        input_schema=FlightDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("flight-details")
    async def get_flight_details(self, input: FlightDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching flight details for offer {input.offerId}")
        return await self.service.get_offer(input.offerId)

    @tool(
        name="search_airports",
        title="Search Airports",
        description="Search for airports by query string.",
        input_schema=AirportSearchInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("airport-search")
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
    @widget("order-summary")
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
        return await self.service.create_order(order_params)

    @tool(
        name="get_order_details",
        title="Get Order Details",
        description="Get detailed information about an order.",
        input_schema=OrderDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("order-summary")
    async def get_order_details(self, input: OrderDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching details for order {input.orderId}")
        return await self.service.get_order(input.orderId)

    @tool(
        name="get_seat_map",
        title="Get Seat Map",
        description="Get available seats for a flight offer to allow seat selection.",
        input_schema=SeatMapInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("seat-selection")
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
    @widget("order-cancellation")
    async def cancel_order(self, input: CancelOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Cancelling order {input.orderId}")
        return await self.service.cancel_order(input.orderId)
