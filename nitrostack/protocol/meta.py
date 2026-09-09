"""Request _meta envelope parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

MCP_META_PREFIX = "io.modelcontextprotocol/"
# Unsigned envelope keys that must not become handler identity.
_IDENTITY_META_KEYS = frozenset(
    {"userid", "user_id", "user", "tenantid", "tenant_id", "tenant"}
)
_PROTOCOL_VERSION_HEADER = "mcp-protocol-version"


@dataclass(frozen=True)
class RequestMeta:
    """Parsed _meta envelope lifted into execution context."""

    protocol_version: Optional[str] = None
    client_info: Optional[dict[str, Any]] = None
    client_capabilities: Optional[dict[str, Any]] = None
    traceparent: Optional[str] = None
    tracestate: Optional[str] = None
    baggage: Optional[str] = None
    trace: Optional[dict[str, Any]] = None
    auth: Optional[Any] = None
    raw: dict[str, Any] = field(default_factory=dict)


def _meta_get(meta: dict[str, Any], bare: str, prefixed: str) -> Optional[Any]:
    if prefixed in meta:
        return meta[prefixed]
    return meta.get(bare)


def extract_request_meta(params: dict[str, Any]) -> RequestMeta:
    """Parse _meta from JSON-RPC params (reverse-DNS and bare keys)."""
    raw = params.get("_meta")
    if not isinstance(raw, dict):
        return RequestMeta()

    protocol_version = _meta_get(raw, "protocolVersion", f"{MCP_META_PREFIX}protocolVersion")
    if not isinstance(protocol_version, str):
        mcp = raw.get("mcp")
        if isinstance(mcp, dict):
            nested = mcp.get("protocolVersion")
            protocol_version = nested if isinstance(nested, str) else None
    client_info = _meta_get(raw, "clientInfo", f"{MCP_META_PREFIX}clientInfo")
    client_capabilities = _meta_get(
        raw, "clientCapabilities", f"{MCP_META_PREFIX}clientCapabilities"
    )
    traceparent = _meta_get(raw, "traceparent", f"{MCP_META_PREFIX}traceparent")
    tracestate = _meta_get(raw, "tracestate", f"{MCP_META_PREFIX}tracestate")
    baggage = _meta_get(raw, "baggage", f"{MCP_META_PREFIX}baggage")
    trace = _meta_get(raw, "trace", f"{MCP_META_PREFIX}trace")
    auth = _meta_get(raw, "auth", f"{MCP_META_PREFIX}auth")

    return RequestMeta(
        protocol_version=protocol_version if isinstance(protocol_version, str) else None,
        client_info=client_info if isinstance(client_info, dict) else None,
        client_capabilities=client_capabilities if isinstance(client_capabilities, dict) else None,
        traceparent=traceparent if isinstance(traceparent, str) else None,
        tracestate=tracestate if isinstance(tracestate, str) else None,
        baggage=baggage if isinstance(baggage, str) else None,
        trace=trace if isinstance(trace, dict) else None,
        auth=auth,
        raw=dict(raw),
    )


def split_params_and_meta(params: dict[str, Any]) -> tuple[dict[str, Any], RequestMeta]:
    """Separate business params from the _meta envelope."""
    meta = extract_request_meta(params)
    business = {key: value for key, value in params.items() if key != "_meta"}
    return business, meta


def _is_envelope_argument_key(key: Any) -> bool:
    name = str(key)
    return name == "_meta" or name.startswith(MCP_META_PREFIX)


def strip_tool_arguments(arguments: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Copy ``tools/call`` arguments without protocol envelope keys.

    Drops ``_meta`` and ``io.modelcontextprotocol/*`` so guards, pipes, and
    user handlers never receive envelope slots as input. Nested user values
    are left unchanged. The request envelope stays on ``ExecutionContext``.
    """
    if not arguments:
        return {}
    cleaned = {
        key: value
        for key, value in arguments.items()
        if not _is_envelope_argument_key(key)
    }
    inner = cleaned.get("input")
    if isinstance(inner, Mapping):
        cleaned["input"] = {
            key: value
            for key, value in inner.items()
            if not _is_envelope_argument_key(key)
        }
    return cleaned


@dataclass(frozen=True)
class RequestEnvelope:
    """Allowed envelope mapping for handler context. Identity is not included."""

    meta: RequestMeta
    mcp_headers: dict[str, str]
    protocol_version: Optional[str]


def bind_request_envelope(
    raw_meta: Optional[dict[str, Any]] = None,
    mcp_headers: Optional[Mapping[str, str]] = None,
) -> RequestEnvelope:
    """
    Map JSON-RPC ``_meta`` and MCP headers onto handler context fields.

    Unsigned ``userId`` / ``tenantId`` remain on ``meta.raw`` only and are not
    treated as identity.
    """
    meta = extract_request_meta({"_meta": raw_meta or {}})
    headers = {key: value for key, value in (mcp_headers or {}).items()}
    header_version = None
    for key, value in headers.items():
        if key.lower() == _PROTOCOL_VERSION_HEADER:
            header_version = value
            break
    return RequestEnvelope(
        meta=meta,
        mcp_headers=headers,
        protocol_version=header_version or meta.protocol_version,
    )


def envelope_identity_is_ignored(raw_meta: dict[str, Any]) -> bool:
    """True when the envelope contains unsigned identity keys."""
    return any(str(key).lower() in _IDENTITY_META_KEYS for key in raw_meta)


def envelope_protocol_version(meta: RequestMeta) -> Optional[str]:
    """Protocol version from ``_meta.mcp.protocolVersion``, then other envelope keys."""
    mcp = meta.raw.get("mcp") if meta.raw else None
    if isinstance(mcp, dict):
        nested = mcp.get("protocolVersion")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    if isinstance(meta.protocol_version, str) and meta.protocol_version.strip():
        return meta.protocol_version.strip()
    return None


def flatten_request_meta_object(raw_meta: Any) -> dict[str, Any]:
    """Flatten an MCP ``_meta`` object, including Pydantic extras."""
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
    return data
