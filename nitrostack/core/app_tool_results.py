"""ToolResults implementation mixin."""

import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from pydantic import BaseModel

from nitrostack.core.app_mode import get_widget_mime_type
from nitrostack.core.context import ExecutionContext
from nitrostack.core.interceptors.data_spillover import (
    SpilloverResult,
)
from nitrostack.protocol.mrtr import InputRequiredResult
from nitrostack.widgets.component import Component
from nitrostack.widgets.mcp_meta import build_call_tool_result_meta

logger = logging.getLogger(__name__)


class ToolResultsMixin:
    def _to_call_tool_result(
        self,
        result: Any,
        component: Optional[Component] = None,
        context: Optional[ExecutionContext] = None,
    ) -> types.CallToolResult:
        if isinstance(result, SpilloverResult):
            return result.to_mcp()
        if isinstance(result, InputRequiredResult):
            wire = result.to_wire_dict()
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=wire.get("message", "Input required"))
                ],
                structuredContent=wire,
                isError=False,
            )
        mrtr_result = None
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            from nitrostack.protocol.mrtr import coerce_input_required_result

            mrtr_result = coerce_input_required_result(result)
        if mrtr_result is not None:
            wire = mrtr_result.to_wire_dict()
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=wire.get("message", "Input required"))
                ],
                structuredContent=wire,
                isError=False,
            )
        if isinstance(result, types.CallToolResult):
            if component is not None:
                result = result.model_copy(
                    update={
                        "meta": build_call_tool_result_meta(
                            component, self._call_tool_result_meta(result)
                        )
                    }
                )
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump()

        structured: Any = result
        result_meta: Optional[Dict[str, Any]] = None

        if component is not None:
            if component.transformer is not None:
                structured = component.transformer(result, context)
            if component.meta_transformer is not None:
                result_meta = component.meta_transformer(result, context) or {}
            if isinstance(structured, BaseModel):
                structured = structured.model_dump()

        if isinstance(result, dict) and "content" in result and "isError" in result:
            call_result = types.CallToolResult(**result)
            if component is not None:
                call_result = call_result.model_copy(
                    update={
                        "meta": build_call_tool_result_meta(
                            component, self._call_tool_result_meta(call_result)
                        )
                    }
                )
            return call_result

        if isinstance(structured, dict):
            widget_meta = (
                build_call_tool_result_meta(component, result_meta) if component else result_meta
            )
            return types.CallToolResult(
                content=self._widget_result_content(structured, component),
                structuredContent=structured,
                **({"_meta": widget_meta} if widget_meta else {}),
                isError=False,
            )

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(structured))],
            isError=False,
        )

    @staticmethod
    def _call_tool_result_meta(result: types.CallToolResult) -> Optional[Dict[str, Any]]:
        """Read caller ``_meta`` from the field or pydantic extra (alias vs extra)."""
        if result.meta:
            return dict(result.meta)
        extra = getattr(result, "model_extra", None) or {}
        raw = extra.get("_meta") or extra.get("meta")
        return dict(raw) if isinstance(raw, dict) else None

    def _widget_result_content(
        self, structured: Dict[str, Any], component: Optional[Component]
    ) -> List[Any]:
        """Text fallback plus data-filled HTML so Inspector/Studio can paint the live result."""
        content: List[Any] = [
            types.TextContent(type="text", text=json.dumps(structured, indent=2, default=str)),
        ]
        if component is None:
            return content
        mime = get_widget_mime_type()
        try:
            filled = component.html_with_data(structured)
        except Exception:
            logger.exception(
                "Widget HTML render failed for %s; returning JSON without embedded HTML",
                component.id,
            )
            filled = None
        if filled is not None:
            content.append(
                types.EmbeddedResource(
                    type="resource",
                    resource=types.TextResourceContents(
                        uri=component.resource_uri,
                        mimeType=mime,
                        text=filled,
                    ),
                )
            )
        content.append(
            types.ResourceLink(
                type="resource_link",
                uri=component.resource_uri,
                name=component.name,
                description=component.description,
                mimeType=mime,
            )
        )
        return content
