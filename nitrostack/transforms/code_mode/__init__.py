"""Code Mode transform for NitroStack."""
from __future__ import annotations

from nitrostack.transforms.code_mode.destructive_guard import (
    DestructiveToolError,
    assert_tool_allowed,
    is_destructive_tool,
)
from nitrostack.transforms.code_mode.legible_error import format_legible_error
from nitrostack.transforms.code_mode.sandbox_interface import SandboxProtocol, ToolsApi
from nitrostack.transforms.code_mode.transform import CodeModeTransform
from nitrostack.transforms.code_mode.types import (
    CodeModeLimits,
    ExecuteScriptInput,
    GetSchemaInput,
    SandboxExecutionResult,
    SearchToolsInput,
)
from nitrostack.transforms.code_mode.worker_pool import WorkerPool

__all__ = [
    "CodeModeTransform",
    "CodeModeLimits",
    "SandboxExecutionResult",
    "SandboxProtocol",
    "ToolsApi",
    "WorkerPool",
    "is_destructive_tool",
    "assert_tool_allowed",
    "DestructiveToolError",
    "format_legible_error",
    "SearchToolsInput",
    "GetSchemaInput",
    "ExecuteScriptInput",
]
