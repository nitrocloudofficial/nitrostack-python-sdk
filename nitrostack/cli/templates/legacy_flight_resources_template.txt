from nitrostack import injectable, resource, ExecutionContext
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
        return "# Flight Booking Guide\n\n1. Search flights.\n2. Collect passenger info.\n3. Create order hold."
