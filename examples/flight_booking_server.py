# Flight Booking MCP Server Example (with OAuth 2.1)
#
# This example demonstrates how to protect MCP tools using OAuth 2.1.
# The tools are protected by the @use_guards(OAuthGuard, create_scope_guard([...]))
# decorators, requiring a valid OAuth token from the configured introspection endpoint or JWKS provider.
#
# To run this example, configure the OAuth settings in your .env file:
#   PORT=3000
#   RESOURCE_URI=https://mcplocal
#   AUTH_SERVER_URL=https://dev-5dt0utuk315713tjm.us.auth0.com
#   OAUTH_INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
#   # Or using JWKS:
#   # JWKS_URI=http://localhost:3000/oauth/jwks
#   # TOKEN_AUDIENCE=https://mcplocal
#   # TOKEN_ISSUER=https://dev-5dt0utuk315713tjm.us.auth0.com

import asyncio

import asyncio
import os
import sys

# Keep direct execution working outside the repository working directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import McpApplicationFactory
from examples.flight_booking.models import (
    SearchFlightsInput,
    FlightDetailsInput,
    AirportSearchInput,
    CreateOrderInput,
    OrderDetailsInput,
    SeatMapInput,
    CancelOrderInput,
    GetAirlinesInput,
)
from examples.flight_booking.guards import (
    create_scope_guard,
)
from examples.flight_booking.service import (
    DuffelService,
)
from examples.flight_booking.tools import (
    FlightTools,
    BookingTools,
)
from examples.flight_booking.definitions import (
    FlightPrompts,
    FlightResources,
    SystemHealthCheck,
)
from examples.flight_booking.application import (
    FlightsModule,
    AppModule,
    App,
)


async def main():
    sys.stderr.write("🔐 Starting Airline Ticketing MCP Server with OAuth 2.1...\n")
    sys.stderr.flush()
    app = await McpApplicationFactory.create(App)
    await app.start()


if __name__ == "__main__":
    asyncio.run(main())
