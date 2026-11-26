from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import mcp.types as types

from nitrostack.transforms.code_mode.synthetic_tools import create_code_mode_tools
from nitrostack.transforms.code_mode.types import CodeModeLimits
from nitrostack.transforms.code_mode.worker_pool import WorkerPool
from nitrostack.transforms.synthetic import SyntheticTool
from nitrostack.transforms.transform import CatalogTransform, NextResolver, TransformContext

logger = logging.getLogger(__name__)


class CodeModeTransform(CatalogTransform):
    """Replaces the catalog with synthetic tools (search, get_schema, execute) for Code Mode."""

    name = "code_mode"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        search_tool_name: str = "search",
        get_schema_tool_name: str = "get_schema",
        execute_tool_name: str = "execute",
        limits: Optional[CodeModeLimits] = None,
        sandbox: Optional[Any] = None,
        always_visible: Iterable[str] = (),
        timeout_ms: Optional[int] = None,
        memory_limit_mb: Optional[int] = None,
        max_tool_calls: Optional[int] = None,
        allow_destructive: Optional[bool] = None,
        worker_pool_size: Optional[int] = None,
    ) -> None:
        if name:
            self.name = name

        # Validate unique tool names
        tool_names = [search_tool_name, get_schema_tool_name, execute_tool_name]
        if len(set(tool_names)) != len(tool_names) or any(not n.strip() for n in tool_names):
            raise ValueError("search, get_schema, and execute tool names must be non-empty and unique.")

        self.search_tool_name = search_tool_name
        self.get_schema_tool_name = get_schema_tool_name
        self.execute_tool_name = execute_tool_name
        self.synthetic_names: Set[str] = set(tool_names)
        self.always_visible = frozenset(always_visible)

        # Configure limits
        if limits is not None:
            self.limits = limits
        else:
            self.limits = CodeModeLimits(
                timeout_ms=timeout_ms if timeout_ms is not None else 30000,
                memory_limit_mb=memory_limit_mb if memory_limit_mb is not None else 100,
                max_tool_calls=max_tool_calls if max_tool_calls is not None else 50,
                allow_destructive=allow_destructive if allow_destructive is not None else False,
                worker_pool_size=worker_pool_size if worker_pool_size is not None else 4,
            )

        self._registry: Any = None
        self._sandbox = sandbox
        self._synthetic: Dict[str, SyntheticTool] = create_code_mode_tools(self)

    @property
    def sandbox(self) -> Any:
        if self._sandbox is None:
            self._sandbox = WorkerPool(self.limits)
        return self._sandbox

    def on_register(self, registry: Any) -> None:
        self._registry = registry

    async def dispose(self) -> None:
        if self._sandbox is not None and hasattr(self._sandbox, "dispose"):
            await self._sandbox.dispose()

    def synthetic_tools(self) -> List[SyntheticTool]:
        return list(self._synthetic.values())

    def get_real_tools(self) -> List[Any]:
        if self._registry is None:
            return []
        return [
            t for t in self._registry.list_tools()
            if getattr(t, "name", "") not in self.synthetic_names
        ]

    def get_real_tool(self, name: str) -> Optional[Any]:
        for t in self.get_real_tools():
            if getattr(t, "name", "") == name:
                return t
        return None

    def unwrap_tool_result(self, res: Any) -> Any:
        """Unwrap CallToolResult into a python value or parsed json if applicable."""
        if isinstance(res, types.CallToolResult):
            is_err = getattr(res, "is_error", getattr(res, "isError", False))
            if is_err:
                err_text = ""
                content = getattr(res, "content", []) or []
                for c in content:
                    if hasattr(c, "text"):
                        err_text += c.text
                raise RuntimeError(err_text or "Tool call reported an error.")

            structured = getattr(res, "structured_content", getattr(res, "structuredContent", None))
            if structured is not None:
                return structured

            content = getattr(res, "content", []) or []
            if content:
                for c in content:
                    if hasattr(c, "text") and c.text:
                        try:
                            return json.loads(c.text)
                        except Exception:
                            return c.text
            return None
        return res

    def apply_transform(self, tools: Sequence[Any], ctx: TransformContext) -> List[Any]:
        visible = []
        for t in tools:
            name = getattr(t, "name", "")
            if name in self.synthetic_names:
                continue
            if name in self.always_visible:
                visible.append(t)
        return [s.to_mcp_tool() for s in self._synthetic.values()] + visible

    async def resolve_tool(
        self, name: str, next: NextResolver, ctx: TransformContext
    ) -> Optional[Any]:
        if name in self._synthetic:
            return self._synthetic[name]
        return await next(name)
