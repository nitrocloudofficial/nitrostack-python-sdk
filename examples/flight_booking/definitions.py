"""Flight-booking example definitions."""

import time
from nitrostack import prompt, resource, injectable, ExecutionContext, health_check
from .service import DuffelService


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
        return "# Flight Booking Guide\n\n1. Search flights.\n2. Collect passenger info.\n3. Create order hold."


class SystemHealthCheck:
    def __init__(self):
        self.start_time = time.time()

    @health_check("system")
    def check_system(self) -> bool:
        uptime = time.time() - self.start_time
        return uptime >= 0
