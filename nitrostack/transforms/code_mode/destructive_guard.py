from __future__ import annotations

from typing import Any, Optional


class DestructiveToolError(Exception):
    """Raised when a destructive tool is called while allowDestructive is false."""
    pass


def _get_annotation(annotations: Any, *keys: str) -> Optional[bool]:
    if annotations is None:
        return None
    for k in keys:
        if isinstance(annotations, dict):
            if k in annotations:
                return annotations[k]
        elif hasattr(annotations, k):
            return getattr(annotations, k)
    return None


def is_destructive_tool(tool: Any) -> bool:
    """A tool is destructive unless readOnlyHint is true or destructiveHint is explicitly false."""
    annotations = getattr(tool, "annotations", None)
    if annotations is None and hasattr(tool, "config"):
        annotations = getattr(tool.config, "annotations", None)

    read_only = _get_annotation(annotations, "readOnlyHint", "read_only_hint", "read_only")
    if read_only is True:
        return False

    destructive = _get_annotation(annotations, "destructiveHint", "destructive_hint")
    if destructive is False:
        return False

    return True


def assert_tool_allowed(tool: Any, allow_destructive: bool) -> None:
    """Reject tool if destructive and allow_destructive is False."""
    if not allow_destructive and is_destructive_tool(tool):
        name = getattr(tool, "name", str(tool))
        raise DestructiveToolError(
            f"Tool '{name}' is destructive and cannot be executed in code-mode when allowDestructive is false."
        )
