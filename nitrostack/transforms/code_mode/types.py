from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


DEFAULT_TIMEOUT_MS = 30000
DEFAULT_MEMORY_LIMIT_MB = 100
MAX_MEMORY_LIMIT_MB = 128
DEFAULT_MAX_TOOL_CALLS = 50
DEFAULT_ALLOW_DESTRUCTIVE = False
DEFAULT_POOL_SIZE = 4


@dataclass
class CodeModeLimits:
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    allow_destructive: bool = DEFAULT_ALLOW_DESTRUCTIVE
    worker_pool_size: int = DEFAULT_POOL_SIZE

    def __post_init__(self) -> None:
        if self.memory_limit_mb > MAX_MEMORY_LIMIT_MB:
            self.memory_limit_mb = MAX_MEMORY_LIMIT_MB
        if self.memory_limit_mb <= 0:
            raise ValueError("memory_limit_mb must be positive")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be non-negative")
        if self.worker_pool_size <= 0:
            raise ValueError("worker_pool_size must be positive")


@dataclass
class SandboxExecutionResult:
    success: bool
    value: Any = None
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    tool_call_count: int = 0
    duration_ms: float = 0.0
    memory_used_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "value": self.value,
            "error": self.error,
            "logs": self.logs,
            "toolCallCount": self.tool_call_count,
            "durationMs": round(self.duration_ms, 2),
            "memoryUsedMb": round(self.memory_used_mb, 3),
        }


class SearchToolsInput(BaseModel):
    query: str = Field(default="", description="Search query to filter tools, or empty to list all.")


class GetSchemaInput(BaseModel):
    name: Optional[str] = Field(default=None, description="Exact name of tool to get schema for.")
    names: Optional[List[str]] = Field(default=None, description="List of tool names to get schemas for.")


class ExecuteScriptInput(BaseModel):
    code: str = Field(..., description="JavaScript code to execute in the isolated sandbox.")
