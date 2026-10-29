import inspect
import json
from typing import Type, Any, Dict, Optional, List, Tuple

import mcp.types as types
from unittest.mock import NonCallableMock

from nitrostack.core.app import mcp_app, McpApplicationFactory, ServerConfig, McpApplication
from nitrostack.core.di import DIContainer
from nitrostack.testing.mocks import MockLogger, create_mock_context, flush_tasks

__all__ = ["NitroTestingModule", "MockLogger", "create_mock_context", "flush_tasks"]

_MISSING = object()


def _is_factory(value: Any) -> bool:
    # Classes and plain functions are factories; anything else is used as the instance.
    return inspect.isclass(value) or inspect.isroutine(value)


class NitroTestingModule:
    """
    In-process test harness for testing NitroStack applications
    without spinning up real transports or subprocesses (Section 15).

    Dispatches through the owned low-level `mcp.server.lowlevel.Server`
    `request_handlers` (the same dict the real stdio/HTTP transports use).
    """
    def __init__(self, app: McpApplication, _restore: Optional[List[Tuple[Any, Any, Any]]] = None):
        self.app = app
        self._restore = _restore or []
        self._closed = False
    @classmethod
    async def create(
        cls,
        app_module: Type,
        *,
        protocol_era: Optional[str] = None,
        transforms: Optional[list] = None,
        overrides: Optional[Dict[Any, Any]] = None,
    ) -> "NitroTestingModule":
        """Build the app in-process.

        ``overrides`` maps a provider token (class or string) to the instance tools should
        receive. A class or plain function is called with no arguments instead (it may be
        ``async``). The original provider is never constructed. Call ``close()`` (or use
        ``async with``) to undo the overrides.
        """
        container = DIContainer.get_instance()
        restore: List[Tuple[Any, Any, Any]] = []
        for token, override in (overrides or {}).items():
            value = override() if _is_factory(override) else override
            if inspect.isawaitable(value):
                value = await value
            restore.append((
                token,
                container._instances.get(token, _MISSING),
                container._registry.get(token, _MISSING),
            ))
            container.register_value(token, value)

        server_config = ServerConfig(
            name="test-server",
            protocol_era=protocol_era,
        )

        @mcp_app(
            module=app_module,
            server=server_config,
            transforms=transforms,
        )
        class TestApp:
            pass

        try:
            app = await McpApplicationFactory.create(TestApp)
        except BaseException:
            cls._restore_overrides(restore)
            raise
        return cls(app, restore)

    @staticmethod
    def _restore_overrides(restore: List[Tuple[Any, Any, Any]]) -> None:
        container = DIContainer.get_instance()
        for token, instance, registered in reversed(restore):
            for store, previous in ((container._instances, instance), (container._registry, registered)):
                if previous is _MISSING:
                    store.pop(token, None)
                else:
                    store[token] = previous

    async def close(self) -> None:
        """Run each provider's ``on_module_destroy`` hook once, then undo overrides.

        Safe to call more than once. Hooks run in reverse creation order, may be sync or
        async, and every hook runs even if one fails (the first error is raised after).
        """
        if self._closed:
            return
        self._closed = True
        errors: List[BaseException] = []
        try:
            for instance in reversed(getattr(self.app, "_module_instances", [])):
                if isinstance(instance, NonCallableMock):
                    continue
                hook = getattr(instance, "on_module_destroy", None)
                if not callable(hook):
                    continue
                try:
                    result = hook()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001 - run the remaining hooks first
                    errors.append(exc)
        finally:
            self._restore_overrides(self._restore)
        if errors:
            raise errors[0]

    async def __aenter__(self) -> "NitroTestingModule":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

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
