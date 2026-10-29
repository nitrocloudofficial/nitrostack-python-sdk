"""Small test helpers: a recording logger, a mock context and a task flusher."""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from nitrostack.core.context import ExecutionContext


class MockLogger:
    """A ``Logger`` that records calls instead of writing them anywhere."""

    def __init__(self) -> None:
        self.logs: List[Tuple[str, str, Optional[dict]]] = []

    def _record(self, level: str, message: str, meta: Optional[dict] = None) -> None:
        self.logs.append((level, message, meta))

    def debug(self, message: str, meta: Optional[dict] = None) -> None:
        self._record("debug", message, meta)

    def info(self, message: str, meta: Optional[dict] = None) -> None:
        self._record("info", message, meta)

    def warn(self, message: str, meta: Optional[dict] = None) -> None:
        self._record("warn", message, meta)

    warning = warn

    def error(self, message: str, meta: Optional[dict] = None) -> None:
        self._record("error", message, meta)

    def has_log(self, level: str, message: str) -> bool:
        """True when a ``level`` log contains ``message`` as a substring."""
        level = "warn" if level == "warning" else level
        return any(lvl == level and message in text for lvl, text, _ in self.logs)

    def clear(self) -> None:
        self.logs.clear()


def create_mock_context(**overrides: Any) -> ExecutionContext:
    """An ``ExecutionContext`` with test defaults and a ``MockLogger``; any field can be overridden."""
    values: Dict[str, Any] = {"request_id": "test-request", "logger": MockLogger()}
    values.update(overrides)
    return ExecutionContext(**values)


async def flush_tasks(rounds: int = 10) -> None:
    """Yield to the event loop ``rounds`` times so ready tasks (and tasks they start) run."""
    for _ in range(rounds):
        await asyncio.sleep(0)
