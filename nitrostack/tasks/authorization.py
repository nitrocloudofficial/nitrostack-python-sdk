"""Task authorization and multi-tenant isolation."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

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


def _meta_dict(raw_meta: Any) -> dict[str, Any]:
    if raw_meta is None:
        return {}
    data: dict[str, Any] = {}
    extra = getattr(raw_meta, "model_extra", None) or getattr(raw_meta, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        data.update(extra)
    if hasattr(raw_meta, "model_dump"):
        try:
            dumped = raw_meta.model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                nested_extra = dumped.pop("__pydantic_extra__", None)
                if isinstance(nested_extra, dict):
                    data.update(nested_extra)
                data.update(dumped)
        except Exception:
            pass
    elif isinstance(raw_meta, dict):
        data.update(raw_meta)
    else:
        for key in ("authorization", "Authorization", "headers"):
            value = getattr(raw_meta, key, None)
            if value is not None:
                data[key] = value
    return data


def _tenant_from_claims(claims: dict[str, Any]) -> Optional[str]:
    for key in ("tenant_id", "tenantId", "org_id", "orgId"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _bearer_token_from_header(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith("Bearer "):
        token = stripped[len("Bearer ") :].strip()
        return token or None
    return None


def _authorization_from_request_context(rc: Any) -> Optional[str]:
    """Prefer transport headers; fall back to a Bearer token in ``_meta`` only."""
    request = getattr(rc, "request", None)
    headers_obj = getattr(request, "headers", None) if request is not None else None
    if headers_obj is not None:
        try:
            header = headers_obj.get("authorization") or headers_obj.get("Authorization")
            token = _bearer_token_from_header(header)
            if token:
                return token
        except Exception:
            pass

    meta = _meta_dict(getattr(rc, "meta", None))
    token = _bearer_token_from_header(meta.get("authorization") or meta.get("Authorization"))
    if token:
        return token
    headers = meta.get("headers")
    if isinstance(headers, dict):
        return _bearer_token_from_header(headers.get("authorization") or headers.get("Authorization"))
    return None


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

    Identity comes from a verified JWT (HTTP ``Authorization`` or a Bearer
    token in ``_meta``), never from unsigned ``userId`` / ``tenantId`` fields.
    If a Bearer token is present and verification fails, returns an empty
    context so scoped tasks are denied.

    ``None`` is reserved for callers with no request context (internal path).
    """
    if rc is None:
        return None

    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id = _session_id_from_request_context(rc)
    token = _authorization_from_request_context(rc)

    if token:
        try:
            from nitrostack.auth.jwt import JWTService
            from nitrostack.core.di import DIContainer

            payload = DIContainer.get_instance().resolve(JWTService).verify_token(token)
            subject = payload.get("sub")
            if isinstance(subject, str) and subject.strip():
                user_id = subject
            tenant_id = _tenant_from_claims(payload)
        except Exception:
            return TaskAccessContext()

    return TaskAccessContext(
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        session_id=str(session_id) if session_id else None,
    )
