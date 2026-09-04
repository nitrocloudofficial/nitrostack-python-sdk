"""Task subsystem shared types (Doc 00 architecture diagram)."""

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]

TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class TaskAccessContext(BaseModel):
    """Identity metadata for multi-tenant task authorization (Doc 07)."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: Optional[str] = Field(default=None, alias="userId")
    tenant_id: Optional[str] = Field(default=None, alias="tenantId")
    session_id: Optional[str] = Field(default=None, alias="sessionId")


@dataclass
class TaskEntry:
    """Internal server task state. Expanded in Doc 05/06 implementations."""

    task_id: str
    status: TaskStatus = "working"
    result: Any = None
    error: Optional[dict[str, Any]] = None
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
