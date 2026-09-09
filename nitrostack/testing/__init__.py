import json
from typing import Type, Any, Dict, Optional

import mcp.types as types

from nitrostack.core.app import mcp_app, McpApplicationFactory, ServerConfig, McpApplication


class NitroTestingModule:
    """
    In-process test harness for testing NitroStack applications
    without spinning up real transports or subprocesses (Section 15).

    Dispatches through the owned low-level `mcp.server.lowlevel.Server`
    `request_handlers` (the same dict the real stdio/HTTP transports use).
    """
    @classmethod
    async def create(cls, app_module: Type) -> "NitroTestingModule":
        # Construct a dummy App class decorated with @mcp_app
        @mcp_app(module=app_module, server=ServerConfig(name="test-server"))
        class TestApp:
            pass

        app = await McpApplicationFactory.create(TestApp)
        return cls(app)

    def __init__(self, app: McpApplication):
        self.app = app

    @staticmethod
    def _extract_text(text_val: Optional[str]) -> Any:
        if text_val is None:
            return None
        try:
            return json.loads(text_val)
        except Exception:
            return text_val

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Calls a tool by name in the test harness, returning the raw or deserialized result."""
        if not self.app.mcp_server:
            raise RuntimeError("Application has not been bootstrapped.")

        handler = self.app.mcp_server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        response = await handler(request)
        result = response.root

        content_list = getattr(result, "content", None) or []
        if content_list and hasattr(content_list[0], "text"):
            extracted = self._extract_text(content_list[0].text)
            if extracted is not None:
                return extracted
        return result

    async def read_resource(self, uri: str) -> Any:
        """Reads a resource by URI in the test harness, returning raw text or deserialized JSON."""
        if not self.app.mcp_server:
            raise RuntimeError("Application has not been bootstrapped.")

        handler = self.app.mcp_server.request_handlers[types.ReadResourceRequest]
        request = types.ReadResourceRequest(
            method="resources/read",
            params=types.ReadResourceRequestParams(uri=uri),
        )
        response = await handler(request)
        result = response.root

        content_list = result.contents or []
        if content_list and hasattr(content_list[0], "text"):
            extracted = self._extract_text(content_list[0].text)
            if extracted is not None:
                return extracted
        return result

    async def get_prompt(self, name: str, arguments: Dict[str, str]) -> Any:
        """Retrieves a prompt by name in the test harness, returning prompt messages."""
        if not self.app.mcp_server:
            raise RuntimeError("Application has not been bootstrapped.")

        handler = self.app.mcp_server.request_handlers[types.GetPromptRequest]
        request = types.GetPromptRequest(
            method="prompts/get",
            params=types.GetPromptRequestParams(name=name, arguments=arguments),
        )
        response = await handler(request)
        result = response.root
        return result.messages
