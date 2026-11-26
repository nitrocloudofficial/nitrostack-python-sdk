"""One place that validates tool arguments, shared by normal and re-entrant calls."""
from __future__ import annotations

from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError


class ToolArgumentError(ValueError):
    """Arguments did not match the tool's input schema."""

    def __init__(self, tool_name: str, errors: list) -> None:
        super().__init__(f"Invalid arguments for tool '{tool_name}'.")
        self.tool_name = tool_name
        self.errors = errors


def validate_tool_arguments(
    tool_name: str,
    input_model: Optional[Type[BaseModel]],
    arguments: Dict[str, Any],
) -> Any:
    """Return the validated input object (or the raw dict if the tool has no model)."""
    if input_model is None:
        return arguments
    try:
        return input_model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolArgumentError(tool_name, exc.errors(include_url=False)) from exc