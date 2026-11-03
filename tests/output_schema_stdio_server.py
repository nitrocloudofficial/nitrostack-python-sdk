"""Tiny stdio server used by tests/test_tool_output_schema.py (run as a subprocess)."""

import asyncio
from typing import Optional

from pydantic import BaseModel

from nitrostack import ServerConfig, mcp_app, module, tool
from nitrostack.core.app import McpApplication


class Empty(BaseModel):
    pass


class Profile(BaseModel):
    nickname: Optional[str] = None
    age: Optional[int] = None
    name: str = "ada"


class Tools:
    @tool("profile", "Return a profile with empty optional fields", Empty, output_schema=Profile)
    async def profile(self, input, context):
        return Profile()


@module(name="schema-wire", providers=[Tools])
class Root:
    pass


@mcp_app(module=Root, server=ServerConfig(name="schema-wire", protocol_era="auto"))
class App:
    pass


async def main():
    from nitrostack.transports.stdio import run_stdio, safe_stdio_transport

    with safe_stdio_transport():
        await run_stdio(McpApplication(App).mcp_server, "auto")


if __name__ == "__main__":
    asyncio.run(main())
