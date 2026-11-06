"""Flight-booking example application."""

import os
from nitrostack import module, mcp_app, ServerConfig
from nitrostack.auth.oauth import OAuthModule
from .service import DuffelService
from .tools import FlightTools, BookingTools
from .definitions import FlightPrompts, FlightResources, SystemHealthCheck


@module(
    name="flights",
    controllers=[FlightTools, BookingTools, FlightPrompts, FlightResources],
    providers=[DuffelService],
    exports=[DuffelService]
)
class FlightsModule:
    pass


@module(
    name="app",
    imports=[
        # Configure OAuth resource protection
        OAuthModule.for_root(
            resource_uri="https://mcplocal",
            authorization_servers=["https://dev-5dt0utuk315713tjm.us.auth0.com"],
            scopes_supported=["read", "write", "admin"],
            token_introspection_endpoint=os.environ.get("INTROSPECTION_ENDPOINT")
        ),
        FlightsModule
    ],
    controllers=[],
    providers=[SystemHealthCheck],
    exports=[]
)
class AppModule:
    pass


@mcp_app(
    module=AppModule,
    server=ServerConfig(name="airline-ticketing-server", version="1.0.0")
)
class App:
    pass
