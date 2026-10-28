from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nitrostack.transforms.code_mode.types import CodeModeLimits, SandboxExecutionResult


@runtime_checkable
class ToolsApi(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        ...


@runtime_checkable
class SandboxProtocol(Protocol):
    async def run(
        self,
        code: str,
        tools_api: Any,
        limits: CodeModeLimits,
    ) -> SandboxExecutionResult:
        ...
