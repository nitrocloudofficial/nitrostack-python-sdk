"""Shared behavior for search transforms: two meta-tools, browse mode, limits, dispatch."""
from __future__ import annotations

import logging
import re
from abc import abstractmethod
from typing import Any, Dict, Iterable, List, Optional, Sequence

from nitrostack.transforms.search.models import (
    BROWSE_MAX_RESULTS, MAX_SEARCH_RESULTS, CallToolInput, RankOutcome, SearchToolsInput,
)
from nitrostack.transforms.search.serializer import is_always_visible, serialize_tool
from nitrostack.transforms.synthetic import SyntheticTool
from nitrostack.transforms.transform import (
    CatalogTransform, NextResolver, TransformContext,
)
from nitrostack.transforms.validation import ToolArgumentError

logger = logging.getLogger(__name__)

# Words that name no task. A query made only of these (or empty) lists the catalog.
_BROWSE_WORDS = frozenset({
    "the", "all", "show", "list", "me", "tools", "tool", "available", "catalog",
    "everything", "please", "what", "do", "you", "have", "browse",
})
_PLAIN_QUERY = re.compile(r"[\w\s]+")


def is_browse_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if not _PLAIN_QUERY.fullmatch(q):
        return False  # has punctuation or regex syntax: it is a real search
    return all(tok in _BROWSE_WORDS for tok in q.casefold().split())


class BaseSearchTransform(CatalogTransform):
    """Replaces the catalog with ``search_tools`` and ``call_tool`` (plus always-visible tools)."""

    name = "search"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
        always_visible: Iterable[str] = (),
    ) -> None:
        if name:
            self.name = name
        if not search_tool_name or not call_tool_name or search_tool_name == call_tool_name:
            raise ValueError("search_tool_name and call_tool_name must be non-empty and different.")
        self.search_tool_name = search_tool_name
        self.call_tool_name = call_tool_name
        self.always_visible = frozenset(always_visible)
        self._registry: Any = None
        self._synthetic: Dict[str, SyntheticTool] = {
            search_tool_name: SyntheticTool(
                name=search_tool_name,
                description=(
                    "Find tools by what they do. Returns matching tool names with their "
                    "parameters. Use call_tool to run one. An empty query lists the catalog."
                ),
                input_model=SearchToolsInput,
                handler=self._handle_search,
                read_only=True,
            ),
            call_tool_name: SyntheticTool(
                name=call_tool_name,
                description="Run a tool found with search_tools, by exact name, with its arguments.",
                input_model=CallToolInput,
                handler=self._handle_call,
            ),
        }

    # ---- lifecycle -------------------------------------------------------

    def on_register(self, registry: Any) -> None:
        self._registry = registry

    def synthetic_tools(self) -> List[SyntheticTool]:
        return list(self._synthetic.values())

    # ---- catalog hooks ---------------------------------------------------

    def apply_transform(self, tools: Sequence[Any], ctx: TransformContext) -> List[Any]:
        visible = []
        for tool in tools:
            if tool.name in self._synthetic:
                logger.warning("Real tool %r is shadowed by the %s meta-tool.", tool.name, self.name)
                continue
            if is_always_visible(tool, self.always_visible):
                visible.append(tool)
        return [s.to_mcp_tool() for s in self._synthetic.values()] + visible

    async def resolve_tool(self, name: str, next: NextResolver, ctx: TransformContext) -> Optional[Any]:
        if name in self._synthetic:
            return self._synthetic[name]
        return await next(name)

    # ---- ranking (subclasses) -------------------------------------------

    @abstractmethod
    async def rank(self, query: str, tools: Sequence[Any]) -> RankOutcome:
        """Return matching tools, best first. Must not raise for a bad query."""

    # ---- handlers --------------------------------------------------------

    def _catalog(self, ctx: Optional[TransformContext] = None) -> List[Any]:
        if ctx is not None and ctx.visible_tools is not None:
            tools = list(ctx.visible_tools)
        elif self._registry is not None:
            tools = self._registry.list_tools()
        else:
            return []
        tools = [t for t in tools if t.name not in self._synthetic]
        return sorted(tools, key=lambda t: t.name)

    async def _handle_search(self, args: SearchToolsInput, ctx: TransformContext) -> Dict[str, Any]:
        catalog = self._catalog(ctx)
        notes: List[str] = []

        if is_browse_query(args.query):
            shown = catalog[:BROWSE_MAX_RESULTS]
            if len(catalog) > len(shown):
                notes.append(f"Showing the first {len(shown)} of {len(catalog)} tools; search to narrow down.")
            return {
                "mode": "browse",
                "total_tools": len(catalog),
                "results": [serialize_tool(t, "brief") for t in shown],
                "note": " ".join(notes) or None,
            }

        limit = args.limit
        if limit > MAX_SEARCH_RESULTS:
            notes.append(f"limit was reduced from {limit} to {MAX_SEARCH_RESULTS}.")
            limit = MAX_SEARCH_RESULTS

        outcome = await self.rank(args.query, catalog)
        if outcome.note:
            notes.append(outcome.note)
        top = outcome.ranked[:limit]
        if not top:
            notes.append("No tools matched. Try fewer words, or search with an empty query to browse.")
        return {
            "mode": "search",
            "query": args.query,
            "total_tools": len(catalog),
            "results": [serialize_tool(tool, args.detail) for tool, _score in top],
            "note": " ".join(notes) or None,
        }

    async def _handle_call(self, args: CallToolInput, ctx: TransformContext) -> Any:
        target = args.name.strip()
        if target in self._synthetic:
            raise ToolArgumentError(
                self.call_tool_name,
                [{"loc": ["name"], "msg": f"'{target}' is a discovery tool and cannot be called through {self.call_tool_name}."}],
            )
        # Goes back through the pipeline: guards and argument validation run for the real tool,
        # and this transform is skipped for the nested call.
        return await ctx.call_tool(target, args.arguments, caller=self.name)
