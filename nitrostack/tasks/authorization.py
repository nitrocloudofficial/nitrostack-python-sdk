"""Task authorization and multi-tenant isolation."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from nitrostack.core.errors import TaskNotFoundError
from nitrostack.tasks.types import TaskAccessContext, TaskEntry, TaskWireData


def check_task_access(entry: TaskEntry, context: TaskAccessContext | None) -> None:
    """
    Enforce tenant/user/session isolation for task access.

    Raises ``TaskNotFoundError`` (not Forbidden) on mismatch — anti-enumeration.
    """
    if context is None:
        return

    task_id = entry.data.task_id

    if entry.tenant_id and context.tenant_id and entry.tenant_id != context.tenant_id:
        raise TaskNotFoundError(task_id)

    if entry.owner_id and context.user_id and entry.owner_id != context.user_id:
        raise TaskNotFoundError(task_id)

    if entry.session_id and context.session_id and entry.session_id != context.session_id:
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
        for key in ("userId", "user_id", "tenantId", "tenant_id", "sessionId", "session_id", "authorization", "headers"):
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


def extract_task_access_context(rc: Any) -> Optional[TaskAccessContext]:
    """
    Build ``TaskAccessContext`` from an MCP request context.

    Reads explicit ``userId`` / ``tenantId`` / ``sessionId`` from ``_meta``, then
    falls back to verified JWT claims when ``JWTService`` is registered.
    """
    if rc is None:
        return None

    meta = _meta_dict(getattr(rc, "meta", None))
    user_id = meta.get("userId") or meta.get("user_id")
    tenant_id = meta.get("tenantId") or meta.get("tenant_id")
    session_id = meta.get("sessionId") or meta.get("session_id")

    session = getattr(rc, "session", None)
    if session is not None and not session_id:
        session_id = getattr(session, "id", None) or getattr(session, "session_id", None)
        if session_id is not None:
            session_id = str(session_id)

    auth_header = meta.get("authorization") or meta.get("Authorization")
    headers = meta.get("headers")
    if isinstance(headers, dict):
        auth_header = auth_header or headers.get("authorization") or headers.get("Authorization")

    request = getattr(rc, "request", None)
    headers_obj = getattr(request, "headers", None) if request is not None else None
    if headers_obj is not None and not auth_header:
        try:
            auth_header = headers_obj.get("authorization") or headers_obj.get("Authorization")
        except Exception:
            pass

    if isinstance(auth_header, str) and auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :].strip()
        if token:
            try:
                from nitrostack.core.di import DIContainer
                from nitrostack.auth.jwt import JWTService

                payload = DIContainer.get_instance().resolve(JWTService).verify_token(token)
                user_id = user_id or payload.get("sub")
                tenant_id = tenant_id or _tenant_from_claims(payload)
            except Exception:
                pass

    if not any([user_id, tenant_id, session_id]):
        return None

    return TaskAccessContext(
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        session_id=str(session_id) if session_id else None,
    )
