import inspect
import json
from typing import Type, Any, Dict, Optional, List, Tuple

import mcp.types as types
from unittest.mock import NonCallableMock

from nitrostack.core.app import mcp_app, McpApplicationFactory, ServerConfig, McpApplication
from nitrostack.core.di import DIContainer
from nitrostack.testing.mocks import MockLogger, create_mock_context, flush_tasks

__all__ = ["NitroTestingModule", "MockLogger", "create_mock_context", "flush_tasks"]


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
    def __init__(
        self,
        app: McpApplication,
        _container: Optional[DIContainer] = None,
        _snapshot: Optional[Tuple[Dict[Any, Any], Dict[Any, Any]]] = None,
        _overrides: Optional[List[Any]] = None,
    ):
        self.app = app
        self._container = _container
        self._snapshot = _snapshot
        self._overrides = _overrides or []
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
        ``async with``) to put the DI container back as it was before ``create()``.
        """
        container = DIContainer.get_instance()
        snapshot = (dict(container._instances), dict(container._registry))
        override_values: List[Any] = []
        try:
            for token, override in (overrides or {}).items():
                value = override() if _is_factory(override) else override
                if inspect.isawaitable(value):
                    value = await value
                container.register_value(token, value)
                override_values.append(value)
            cls._evict_dependents(container, list((overrides or {})))

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

            app = await McpApplicationFactory.create(TestApp)
        except BaseException:
            cls._restore_container(container, snapshot)
            raise
        return cls(app, container, snapshot, override_values)

    @staticmethod
    def _evict_dependents(container: DIContainer, overridden: List[Any]) -> None:
        """Drop cached consumers of an overridden token so they are rebuilt with the override.

        A service built before ``create()`` still holds the real dependency. Evicting it (and
        anything that depends on it) makes the container construct it again inside the
        harness. The snapshot taken first puts the original instances back on ``close()``.
        """
        stale = set(overridden)
        changed = True
        while changed:
            changed = False
            for token, instance in list(container._instances.items()):
                if token in stale:
                    continue
                deps = getattr(type(instance), "_mcp_deps", None) or []
                if any(dep in stale for dep in deps):
                    stale.add(token)
                    del container._instances[token]
                    changed = True

    @staticmethod
    def _restore_container(
        container: DIContainer, snapshot: Tuple[Dict[Any, Any], Dict[Any, Any]]
    ) -> None:
        # Undo everything create() added: overrides and every provider built from them,
        # so a later harness never reuses a service that still holds an old fake.
        for store, saved in zip((container._instances, container._registry), snapshot):
            store.clear()
            store.update(saved)

    def _created_instances(self) -> List[Any]:
        """Overrides first, then every provider this harness built, dependencies before consumers."""
        if self._container is None or self._snapshot is None:
            return list(self._overrides)
        # Compare by identity, not token: a consumer evicted by create() and rebuilt has the
        # same token as the original, but it is a new object this harness must tear down.
        original_ids = {id(value) for value in self._snapshot[0].values()}
        built = [
            value for value in self._container._instances.values()
            if id(value) not in original_ids
        ]
        ordered: List[Any] = []
        seen = set()
        for value in [*self._overrides, *built]:
            if id(value) not in seen:
                seen.add(id(value))
                ordered.append(value)
        return ordered

    async def close(self) -> None:
        """Run each provider's ``on_module_destroy`` hook once, then restore the container.

        Safe to call more than once. Providers are destroyed in reverse creation order, so
        a consumer is destroyed before the dependencies it was built with, including
        indirect ones. Hooks may be sync or async, and every hook runs even if one fails
        (the first error is raised after).
        """
        if self._closed:
            return
        self._closed = True
        errors: List[BaseException] = []
        try:
            for instance in reversed(self._created_instances()):
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
            if self._container is not None and self._snapshot is not None:
                self._restore_container(self._container, self._snapshot)
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
