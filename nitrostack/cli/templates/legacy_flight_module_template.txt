from nitrostack import module
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
