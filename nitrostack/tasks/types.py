"""Task subsystem shared types (Doc 05/06)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]

TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def datetime_to_ms(value: datetime.datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return int(value.timestamp() * 1000)


class TaskAccessContext(BaseModel):
    """Identity metadata for multi-tenant task authorization (Doc 07)."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: Optional[str] = Field(default=None, alias="userId")
    tenant_id: Optional[str] = Field(default=None, alias="tenantId")
    session_id: Optional[str] = Field(default=None, alias="sessionId")


@dataclass
class TaskWireData:
    """Protocol-visible task metadata (Doc 06 §3.1)."""

    task_id: str
    status: TaskStatus = "working"
    status_message: Optional[str] = None
    created_at: datetime.datetime = field(default_factory=utc_now)
    last_updated_at: datetime.datetime = field(default_factory=utc_now)
    ttl_ms: Optional[int] = None
    poll_interval_ms: int = 2000
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.last_updated_at is None:
            self.last_updated_at = self.created_at

    @property
    def last_updated_at_ms(self) -> int:
        return datetime_to_ms(self.last_updated_at)


@dataclass
class TaskEntry:
    """Internal server task state persisted by ``TaskStore`` (Doc 06 §3.2)."""

    task_id: str
    data: TaskWireData
    status: TaskStatus = "working"
    result: Any = None
    error: Optional[dict[str, Any]] = None
    tool_name: Optional[str] = None
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.data.task_id != self.task_id:
            self.data.task_id = self.task_id
        if self.status != self.data.status:
            self.data.status = self.status
