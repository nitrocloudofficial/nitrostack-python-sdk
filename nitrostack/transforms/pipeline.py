"""Ordered execution of transforms for tools/list and tools/call resolution."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional, Sequence

from nitrostack.transforms.transform import (
    NextResolver, Tool, TransformContext, active_bypass, maybe_await,
)

logger = logging.getLogger(__name__)

BaseResolver = Callable[[str], Any]  # returns Tool | None (sync or async)


class TransformError(Exception):
    """A transform hook raised. The app maps this to a JSON-RPC error."""

    def __init__(self, transform_name: str, hook: str) -> None:
        super().__init__(f"Transform '{transform_name}' failed in {hook}.")
        self.transform_name = transform_name
        self.hook = hook


class TransformRegistry:
    """Read-only view passed to ``on_register``."""

    def __init__(self, list_tools: Callable[[], Sequence[Tool]]) -> None:
        self._list_tools = list_tools

    def list_tools(self) -> List[Tool]:
        return list(self._list_tools())


class TransformPipeline:
    def __init__(self, transforms: Optional[Sequence[Any]] = None) -> None:
        self._transforms: List[Any] = list(transforms or [])
        names = [t.name for t in self._transforms]
        if len(set(names)) != len(names):
            raise ValueError(f"Transform names must be unique, got {names}")

    @property
    def enabled(self) -> bool:
        return bool(self._transforms)

    def register(self, registry: TransformRegistry) -> None:
        for t in self._transforms:
            try:
                t.on_register(registry)
            except Exception as exc:
                raise TransformError(t.name, "on_register") from exc

    async def transform_tools(
        self, tools: Sequence[Tool], ctx: TransformContext
    ) -> List[Tool]:
        current = list(tools)
        if not self._transforms:
            return current
        skipped = active_bypass()
        for t in self._transforms:
            if t.name in skipped:
                continue
            try:
                current = list(await maybe_await(t.transform_tools(current, ctx)))
            except TransformError:
                raise
            except Exception as exc:
                logger.exception("Transform %s failed in transform_tools", t.name)
                raise TransformError(t.name, "transform_tools") from exc
        return current

    async def resolve_tool(
        self, name: str, base: BaseResolver, ctx: TransformContext
    ) -> Optional[Tool]:
        """First declared transform is outermost; each chains through ``next``.

        ``base`` must only look the tool up (return None if unknown). Guards and
        argument validation happen after resolution, in the app's invoker.
        """
        if not self._transforms:
            return await maybe_await(base(name))

        transforms = self._transforms

        async def run(index: int, tool_name: str) -> Optional[Tool]:
            skipped = active_bypass()
            while index < len(transforms) and transforms[index].name in skipped:
                index += 1
            if index >= len(transforms):
                return await maybe_await(base(tool_name))
            t = transforms[index]

            async def nxt(n: str) -> Optional[Tool]:
                return await run(index + 1, n)

            try:
                return await maybe_await(t.resolve_tool(tool_name, nxt, ctx))
            except TransformError:
                raise
            except Exception as exc:
                logger.exception("Transform %s failed in resolve_tool", t.name)
                raise TransformError(t.name, "resolve_tool") from exc

        return await run(0, name)

    async def dispose(self) -> None:
        """Dispose in reverse order; one failure must not stop the others."""
        for t in reversed(self._transforms):
            try:
                await maybe_await(t.dispose())
            except Exception:
                logger.exception("Transform %s failed in dispose", t.name)