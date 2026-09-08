"""Task authorization and multi-tenant isolation."""

from __future__ import annotations

from typing import List, Optional, Tuple

from nitrostack.auth.request import (
    authorization_token_from_request,
    tenant_from_claims,
    verify_bearer_payload,
)
from nitrostack.core.errors import TaskNotFoundError
from nitrostack.tasks.types import TaskAccessContext, TaskEntry, TaskWireData


def check_task_access(entry: TaskEntry, context: TaskAccessContext | None) -> None:
    """
    Enforce tenant/user/session isolation for task access.

    ``context is None`` is the internal, non-HTTP path (TaskManager progress
    / complete / fail). HTTP handlers must pass a ``TaskAccessContext`` —
    possibly empty — so missing identity cannot read a scoped task.

    When the task was created with owner/tenant/session, every set dimension
    must match. A missing dimension on the caller is a mismatch.

    Raises ``TaskNotFoundError`` (not Forbidden) on mismatch — anti-enumeration.
    """
    if context is None:
        return

    task_id = entry.data.task_id

    if entry.tenant_id and entry.tenant_id != context.tenant_id:
        raise TaskNotFoundError(task_id)

    if entry.owner_id and entry.owner_id != context.user_id:
        raise TaskNotFoundError(task_id)

    if entry.session_id and entry.session_id != context.session_id:
        raise TaskNotFoundError(task_id)


def entry_matches_access_context(entry: TaskEntry, context: TaskAccessContext | None) -> bool:
    """Return True when ``entry`` is visible to ``context`` (list filtering)."""
    if context is None:
        return True
    try:
        check_task_access(entry, context)
        return True
    except TaskNotFoundError:
        return False


def list_task_wire_data_for_context(
    entries: List[TaskEntry],
    context: TaskAccessContext | None,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> Tuple[List[TaskWireData], Optional[str]]:
    """
    Filter, sort, and paginate task entries for a caller context.
    """
    filtered = [entry for entry in entries if entry_matches_access_context(entry, context)]
    filtered.sort(key=lambda entry: entry.data.created_at, reverse=True)

    start_index = 0
    if cursor:
        cursor_index = next(
            (index for index, entry in enumerate(filtered) if entry.task_id == cursor),
            None,
        )
        if cursor_index is None:
            raise TaskNotFoundError(cursor)
        start_index = cursor_index + 1

    page_entries = filtered[start_index : start_index + limit]
    page = [entry.data for entry in page_entries]
    next_cursor = page[-1].task_id if (start_index + limit) < len(filtered) else None
    return page, next_cursor


def _tenant_from_claims(claims: dict[str, Any]) -> Optional[str]:
    return tenant_from_claims(claims)


def _authorization_from_request_context(rc: Any) -> Optional[str]:
    """HTTP Authorization, then envelope auth, then ``_meta`` Bearer."""
    return authorization_token_from_request(rc)


def _session_id_from_request_context(rc: Any) -> Optional[str]:
    session = getattr(rc, "session", None)
    if session is None:
        return None
    session_id = getattr(session, "id", None) or getattr(session, "session_id", None)
    if session_id is None:
        return None
    return str(session_id)


def extract_task_access_context(rc: Any) -> Optional[TaskAccessContext]:
    """
    Build ``TaskAccessContext`` from an MCP request context.

    Identity comes from a verified JWT: HTTP ``Authorization``, then the
    spec envelope auth slot, then a Bearer token in ``_meta``. Unsigned
    ``userId`` / ``tenantId`` fields are never used. If a Bearer token is
    present and verification fails, returns an empty context so scoped
    tasks are denied.

    ``None`` is reserved for callers with no request context (internal path).
    """
    if rc is None:
        return None

    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id = _session_id_from_request_context(rc)
    token = _authorization_from_request_context(rc)

    if token:
        payload = verify_bearer_payload(token)
        if payload is None:
            return TaskAccessContext()
        subject = payload.get("sub")
        if isinstance(subject, str) and subject.strip():
            user_id = subject
        tenant_id = _tenant_from_claims(payload)

    return TaskAccessContext(
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        session_id=str(session_id) if session_id else None,
    )
