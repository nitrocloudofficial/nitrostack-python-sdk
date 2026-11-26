"""Isolated template bootstrap and real stdio server for registry integration tests."""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def template_manifest(template):
    # Fail if bootstrap accidentally tries to contact any external service.
    def no_network(*args, **kwargs):
        raise AssertionError("Template bootstrap must not make network requests")

    socket.socket.connect = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    os.environ["DUFFEL_API_KEY"] = "test"
    path = ROOT / "nitrostack" / "templates" / template
    sys.path.insert(0, str(path))
    os.chdir(path)
    from app_module import AppModule
    from nitrostack.core.app import McpApplication

    app = McpApplication(AppModule)
    return app.widget_registry.get_manifest().to_dict()


async def stdio_server():
    from pydantic import BaseModel
    from nitrostack import ExecutionContext, WidgetOptions, module, tool, widget
    from nitrostack.core.app import McpApplication
    from nitrostack.transports.stdio import run_stdio, safe_stdio_transport

    class Empty(BaseModel):
        pass

    class Tools:
        @tool("example", "Example", Empty)
        @widget(WidgetOptions(route="stdio-example", html="<p>Example</p>"))
        async def example(self, input: Empty, context: ExecutionContext):
            raise AssertionError("Listing widgets must not execute tools")

    @module(name="stdio-registry", providers=[Tools])
    class AppModule:
        pass

    with safe_stdio_transport():
        app = McpApplication(AppModule)
        await run_stdio(app.mcp_server, "auto")


if __name__ == "__main__":
    if sys.argv[1] == "stdio":
        asyncio.run(stdio_server())
    else:
        print(json.dumps(template_manifest(sys.argv[1])))
