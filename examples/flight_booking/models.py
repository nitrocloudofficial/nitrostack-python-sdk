"""Flight-booking example models."""

from typing import Optional
from pydantic import BaseModel, Field


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


class CreateOrderInput(BaseModel):
    offerId: str = Field(description="The offer ID to book")
    passengers: str = Field(description="JSON string containing array of passenger objects. Each passenger must have: title (mr/ms/mrs/miss/dr), givenName (first name), familyName (last name), gender (M/F), bornOn (YYYY-MM-DD), email, phoneNumber.")


class OrderDetailsInput(BaseModel):
    orderId: str = Field(description="The order ID")


class SeatMapInput(BaseModel):
    offerId: str = Field(description="The offer ID to get seats for")


class CancelOrderInput(BaseModel):
    orderId: str = Field(description="The order ID to cancel")


class GetAirlinesInput(BaseModel):
    pass
