"""Transform protocol, base classes, context and re-entrancy bypass."""
from __future__ import annotations

import contextlib
import contextvars
import inspect
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any, Awaitable, Callable, Dict, Iterator, List, Optional, Protocol,
    Sequence, runtime_checkable,
)

Tool = Any  # mcp.types.Tool in practice; kept loose so transforms may add synthetic tools
NextResolver = Callable[[str], Awaitable[Optional[Tool]]]
ToolInvoker = Callable[[str, Dict[str, Any], "TransformContext"], Awaitable[Any]]

# Names of transforms to skip for the current call chain. Set while a synthetic
# tool calls a real tool, so the issuing transform is not re-applied.
_BYPASS: contextvars.ContextVar[frozenset] = contextvars.ContextVar(
    "nitrostack_transform_bypass", default=frozenset()
)


def active_bypass() -> frozenset:
    return _BYPASS.get()


@contextlib.contextmanager
def bypassing(name: str) -> Iterator[None]:
    token = _BYPASS.set(_BYPASS.get() | {name})
    try:
        yield
    finally:
        _BYPASS.reset(token)


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass
class TransformContext:
    """Per-request context handed to every transform hook."""

    session_id: Optional[str] = None
    subject: Optional[str] = None  # verified bearer subject, never client-supplied
    execution_context: Any = None  # the ExecutionContext of the originating request
    metadata: Dict[str, Any] = field(default_factory=dict)
    visible_tools: Optional[Sequence[Tool]] = None
    _invoker: Optional[ToolInvoker] = None

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        caller: Optional[str] = None,
    ) -> Any:
        """Invoke a real tool from a synthetic tool.

        Goes through the same path as a normal tools/call, so guards and argument
        validation run for the real tool. If ``caller`` is given, that transform
        is skipped for the duration of the call (no re-entrancy loop).
        """
        if self._invoker is None:
            raise RuntimeError("TransformContext has no tool invoker attached.")
        args = arguments or {}
        if caller:
            with bypassing(caller):
                return await self._invoker(name, args, self)
        return await self._invoker(name, args, self)


@runtime_checkable
class McpTransform(Protocol):
    name: str

    def on_register(self, registry: Any) -> Any: ...
    async def dispose(self) -> None: ...
    async def transform_tools(
        self, tools: Sequence[Tool], ctx: TransformContext
    ) -> List[Tool]: ...
    async def resolve_tool(
        self, name: str, next: NextResolver, ctx: TransformContext
    ) -> Optional[Tool]: ...


class McpTransformBase:
    """Convenience base: every hook defaults to a pass-through. Hooks may be sync or async."""

    name: str = "transform"

    def on_register(self, registry: Any) -> None:
        return None

    async def dispose(self) -> None:
        return None

    async def transform_tools(self, tools: Sequence[Tool], ctx: TransformContext) -> List[Tool]:
        return list(tools)

    async def resolve_tool(
        self, name: str, next: NextResolver, ctx: TransformContext
    ) -> Optional[Tool]:
        return await next(name)

    def is_bypassed(self) -> bool:
        return self.name in _BYPASS.get()


class CatalogTransform(McpTransformBase):
    """Base for transforms that rewrite the catalog.

    Subclasses implement ``apply_transform``. While this transform's own synthetic
    tool is calling a real tool, ``transform_tools`` returns the catalog unchanged.
    """

    @abstractmethod
    def apply_transform(
        self, tools: Sequence[Tool], ctx: TransformContext
    ) -> Any:  # list or awaitable of list
        ...

    async def transform_tools(self, tools: Sequence[Tool], ctx: TransformContext) -> List[Tool]:
        if self.is_bypassed():
            return list(tools)
        return list(await maybe_await(self.apply_transform(tools, ctx)))
