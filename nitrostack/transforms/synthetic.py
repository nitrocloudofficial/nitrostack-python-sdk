"""A tool that exists only in a transform, not in the registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Type

import mcp.types as types
from pydantic import BaseModel

from nitrostack.protocol.schema import normalize_input_schema

if TYPE_CHECKING:
    from nitrostack.transforms.transform import TransformContext

# The handler receives the VALIDATED input model instance and the transform context.
SyntheticHandler = Callable[[Any, "TransformContext"], Awaitable[Any]]


@dataclass(frozen=True)
class SyntheticTool:
    name: str
    description: str
    input_model: Type[BaseModel]
    handler: SyntheticHandler
    read_only: bool = False

    def to_mcp_tool(self) -> types.Tool:
        schema = normalize_input_schema(self.input_model.model_json_schema())
        annotations = types.ToolAnnotations(readOnlyHint=True) if self.read_only else None
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=schema,
            annotations=annotations,
        )