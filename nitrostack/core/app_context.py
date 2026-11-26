"""Request context extraction and protocol envelope helpers."""

import logging
from typing import Any, Dict, List, Mapping, Type

import mcp.types as types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import BaseModel

from nitrostack.auth.request import (
    auth_context_from_request,
    bearer_token_from_envelope_auth,
    envelope_auth_slot,
)
from nitrostack.core.context import ExecutionContext
from nitrostack.protocol.meta import (
    bind_request_envelope,
    flatten_request_meta_object,
)
from nitrostack.protocol.observability import TraceContext, extract_trace_context
from nitrostack.runtime.correlation import new_correlation_id
from nitrostack.transports.headers import (
    extract_mcp_param_headers,
    extract_mcp_scope_headers,
    merge_mcp_param_headers,
)

logger = logging.getLogger(__name__)


_AUTH_META_KEYS = ("authorization", "x-api-key", "token", "_oauth", "headers")


def _request_meta_from_ctx(rc: Any) -> Dict[str, Any]:
    """Flatten MCP request ``_meta`` from the low-level request context."""
    if rc is None:
        return {}

    raw_meta = getattr(rc, "meta", None)
    if raw_meta is None:
        return {}
    data = flatten_request_meta_object(raw_meta)
    if data:
        return data
    for key in _AUTH_META_KEYS:
        value = getattr(raw_meta, key, None)
        if value is not None:
            data[key] = value
    return data


def _trace_context_from_request_ctx(rc: Any) -> TraceContext | None:
    return extract_trace_context(_request_meta_from_ctx(rc))


def _read_contents_to_result(
    uri: str, items: List[ReadResourceContents]
) -> types.ReadResourceResult:
    import base64

    contents: List[Any] = []
    for item in items:
        mime = item.mime_type or "text/plain"
        extra = {"_meta": item.meta} if getattr(item, "meta", None) else {}
        if isinstance(item.content, bytes):
            contents.append(
                types.BlobResourceContents(
                    uri=uri,
                    mimeType=mime,
                    blob=base64.b64encode(item.content).decode("ascii"),
                    **extra,
                )
            )
        else:
            contents.append(
                types.TextResourceContents(uri=uri, mimeType=mime, text=str(item.content), **extra)
            )
    return types.ReadResourceResult(contents=contents, resultType="complete")


def _http_headers_from_request_ctx(rc: Any) -> Dict[str, str]:
    if rc is None:
        return {}
    request = getattr(rc, "request", None)
    raw = getattr(request, "headers", None) if request is not None else None
    if raw is None:
        session = getattr(rc, "session", None)
        transport = getattr(session, "transport", None) if session is not None else None
        raw = getattr(transport, "headers", None) if transport is not None else None
    if raw is None:
        raw = getattr(rc, "headers", None)
    if raw is None:
        return {}
    try:
        return {str(key): str(value) for key, value in raw.items()}
    except Exception:
        return {}


def _apply_request_envelope(ctx: ExecutionContext, rc: Any) -> None:
    http_headers = _http_headers_from_request_ctx(rc)
    envelope = bind_request_envelope(
        raw_meta=_request_meta_from_ctx(rc),
        mcp_headers=extract_mcp_scope_headers(http_headers),
    )
    ctx.rpc_meta = envelope.meta
    ctx.mcp_headers = dict(envelope.mcp_headers)
    ctx.mcp_param_headers = extract_mcp_param_headers(http_headers)
    ctx.protocol_version = envelope.protocol_version
    ctx.auth = auth_context_from_request(rc)
    if ctx.trace is None:
        ctx.trace = extract_trace_context(envelope.meta.raw)
    if ctx.jsonrpc_id is None and rc is not None:
        ctx.jsonrpc_id = getattr(rc, "request_id", None)
    if rc is not None and getattr(rc, "correlation_id", None) is None:
        try:
            rc.correlation_id = ctx.correlation_id
        except Exception:
            pass


def _bind_correlation(rc: Any) -> tuple[str, Any]:
    """Allocate a correlation id. Never reuse the client JSON-RPC ``id`` as the key."""
    jsonrpc_id = getattr(rc, "request_id", None) if rc is not None else None
    existing = getattr(rc, "correlation_id", None) if rc is not None else None
    if existing:
        return str(existing), jsonrpc_id
    correlation_id = new_correlation_id()
    if rc is not None:
        try:
            rc.correlation_id = correlation_id
        except Exception:
            pass
    return correlation_id, jsonrpc_id


def _tool_arguments_with_mcp_params(
    arguments: Dict[str, Any],
    param_headers: Mapping[str, str],
    input_model: Type[BaseModel],
) -> Dict[str, Any]:
    """Fill missing tool fields from ``Mcp-Param-*``. Does not rewrite ``name``."""
    payload = dict(arguments or {})
    inner = payload.get("input")
    looks_wrapped = (
        isinstance(inner, dict)
        and set(payload.keys()) <= {"input"}
        and "input" not in input_model.model_fields
    )
    allowed = set(input_model.model_fields)
    if looks_wrapped:
        return {"input": merge_mcp_param_headers(inner, param_headers, allowed_fields=allowed)}
    return merge_mcp_param_headers(payload, param_headers, allowed_fields=allowed)


def _auth_metadata_from_request_ctx(rc: Any) -> Dict[str, Any]:
    """Copy host-sent auth slots from MCP request ``_meta`` into ExecutionContext.

    Real transport headers (``rc.request.headers``) take precedence over
    client-supplied ``_meta`` values: ``_meta`` is part of the JSON-RPC payload
    and fully client-controlled, so it must not override credentials that were
    presented (or vetted) at the transport layer. ``_meta`` remains the only
    source on transports without HTTP headers (e.g. STDIO).
    """
    extra: Dict[str, Any] = {}
    if rc is None:
        return extra

    data = _request_meta_from_ctx(rc)

    auth = data.get("authorization") or data.get("Authorization")
    if isinstance(auth, str) and auth.strip():
        extra["authorization"] = auth
    api_key = data.get("x-api-key")
    if isinstance(api_key, str) and api_key.strip():
        extra["x-api-key"] = api_key
    token = data.get("token")
    if isinstance(token, str) and token.strip():
        extra["token"] = token
    oauth = data.get("_oauth")
    if isinstance(oauth, str) and oauth.strip():
        extra["_oauth"] = oauth
    headers = data.get("headers")
    if isinstance(headers, dict):
        extra["headers"] = headers
        if "authorization" not in extra:
            header_auth = headers.get("authorization") or headers.get("Authorization")
            if isinstance(header_auth, str) and header_auth.strip():
                extra["authorization"] = header_auth
        if "x-api-key" not in extra:
            header_key = headers.get("x-api-key") or headers.get("X-API-Key")
            if isinstance(header_key, str) and header_key.strip():
                extra["x-api-key"] = header_key

    envelope_token = bearer_token_from_envelope_auth(envelope_auth_slot(data))
    if envelope_token:
        extra["authorization"] = f"Bearer {envelope_token}"

    request = getattr(rc, "request", None)
    headers_obj = getattr(request, "headers", None) if request is not None else None
    if headers_obj is not None:
        try:
            http_auth = headers_obj.get("authorization") or headers_obj.get("Authorization")
            if isinstance(http_auth, str) and http_auth.strip():
                extra["authorization"] = http_auth
            http_key = headers_obj.get("x-api-key") or headers_obj.get("X-API-Key")
            if isinstance(http_key, str) and http_key.strip():
                extra["x-api-key"] = http_key
        except Exception:
            pass
    return extra
