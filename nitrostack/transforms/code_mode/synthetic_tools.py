"""Synthetic tools factory and handlers for Code Mode."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from nitrostack.transforms.code_mode.destructive_guard import assert_tool_allowed
from nitrostack.transforms.code_mode.types import (
    CodeModeLimits,
    ExecuteScriptInput,
    GetSchemaInput,
    SearchToolsInput,
)
from nitrostack.transforms.synthetic import SyntheticTool
from nitrostack.transforms.transform import TransformContext

if TYPE_CHECKING:
    from nitrostack.transforms.code_mode.transform import CodeModeTransform


class BoundToolsApi:
    """Tools API exposed to the sandboxed runtime during script execution."""

    def __init__(
        self,
        transform: "CodeModeTransform",
        ctx: TransformContext,
        limits: CodeModeLimits,
    ) -> None:
        self.transform = transform
        self.ctx = ctx
        self.limits = limits
        self.tool_call_count = 0

    async def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        self.tool_call_count += 1
        if self.tool_call_count > self.limits.max_tool_calls:
            raise RuntimeError(f"Tool call limit exceeded ({self.limits.max_tool_calls}).")

        target = (name or "").strip()
        if target in self.transform.synthetic_names:
            raise ValueError(f"Cannot invoke meta-tool '{target}' from code-mode script.")

        # Check destructive guard before execution
        real_tool = self.transform.get_real_tool(target)
        if real_tool is not None:
            assert_tool_allowed(real_tool, self.limits.allow_destructive)

        # Route through TransformContext.call_tool
        # Re-enters the app pipeline so guards and validation run for the real tool,
        # and CodeModeTransform is bypassed for this nested call.
        res = await self.ctx.call_tool(target, arguments, caller=self.transform.name)
        return self.transform.unwrap_tool_result(res)


def create_code_mode_tools(transform: "CodeModeTransform") -> Dict[str, SyntheticTool]:
    """Create the synthetic search, get_schema, and execute tools."""

    async def handle_search(args: SearchToolsInput, ctx: TransformContext) -> Dict[str, Any]:
        catalog = transform.get_real_tools()
        query = (args.query or "").strip().lower()

        results = []
        for t in catalog:
            name = getattr(t, "name", "")
            description = getattr(t, "description", "") or ""
            if not query or query in name.lower() or query in description.lower():
                results.append({
                    "name": name,
                    "description": description,
                })

        return {
            "query": args.query,
            "total_tools": len(catalog),
            "matches": len(results),
            "tools": results,
        }

    async def handle_get_schema(args: GetSchemaInput, ctx: TransformContext) -> Dict[str, Any]:
        catalog = {getattr(t, "name", ""): t for t in transform.get_real_tools()}
        names_to_fetch = set()
        if args.name:
            names_to_fetch.add(args.name.strip())
        if args.names:
            names_to_fetch.update(n.strip() for n in args.names if n.strip())

        schemas: Dict[str, Any] = {}
        for name in names_to_fetch:
            if name in catalog:
                t = catalog[name]
                schema = None
                if hasattr(t, "model_dump"):
                    try:
                        schema = t.model_dump(by_alias=True).get("inputSchema")
                    except Exception:
                        pass
                if schema is None:
                    schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
                if schema is None and hasattr(t, "input_model"):
                    schema = t.input_model.model_json_schema()

                annotations = getattr(t, "annotations", None)
                if annotations is not None and hasattr(annotations, "model_dump"):
                    annotations_dump = annotations.model_dump(by_alias=True, exclude_none=True)
                else:
                    annotations_dump = annotations

                schemas[name] = {
                    "name": name,
                    "description": getattr(t, "description", "") or "",
                    "inputSchema": schema,
                    "annotations": annotations_dump,
                }
            else:
                schemas[name] = {"error": f"Tool '{name}' not found."}

        return {"schemas": schemas}

    async def handle_execute(args: ExecuteScriptInput, ctx: TransformContext) -> Dict[str, Any]:
        tools_api = BoundToolsApi(transform, ctx, transform.limits)
        result = await transform.sandbox.run(args.code, tools_api, transform.limits)
        return result.to_dict()

    return {
        transform.search_tool_name: SyntheticTool(
            name=transform.search_tool_name,
            description="Search available tools in the catalog by query, or list all tools if query is empty.",
            input_model=SearchToolsInput,
            handler=handle_search,
            read_only=True,
        ),
        transform.get_schema_tool_name: SyntheticTool(
            name=transform.get_schema_tool_name,
            description="Get the input JSON schema and parameter specifications for one or more tools.",
            input_model=GetSchemaInput,
            handler=handle_get_schema,
            read_only=True,
        ),
        transform.execute_tool_name: SyntheticTool(
            name=transform.execute_tool_name,
            description="Execute JavaScript code in an isolated sandbox with access to tools API.",
            input_model=ExecuteScriptInput,
            handler=handle_execute,
            read_only=False,
        ),
    }
