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
    client_info = _meta_get(raw, "clientInfo", f"{MCP_META_PREFIX}clientInfo")
    client_capabilities = _meta_get(
        raw, "clientCapabilities", f"{MCP_META_PREFIX}clientCapabilities"
    )
    traceparent = _meta_get(raw, "traceparent", f"{MCP_META_PREFIX}traceparent")
    tracestate = _meta_get(raw, "tracestate", f"{MCP_META_PREFIX}tracestate")
    baggage = _meta_get(raw, "baggage", f"{MCP_META_PREFIX}baggage")
    trace = _meta_get(raw, "trace", f"{MCP_META_PREFIX}trace")

    return RequestMeta(
        protocol_version=protocol_version if isinstance(protocol_version, str) else None,
        client_info=client_info if isinstance(client_info, dict) else None,
        client_capabilities=client_capabilities if isinstance(client_capabilities, dict) else None,
        traceparent=traceparent if isinstance(traceparent, str) else None,
        tracestate=tracestate if isinstance(tracestate, str) else None,
        baggage=baggage if isinstance(baggage, str) else None,
        trace=trace if isinstance(trace, dict) else None,
        raw=dict(raw),
    )


def split_params_and_meta(params: dict[str, Any]) -> tuple[dict[str, Any], RequestMeta]:
    """Separate business params from the _meta envelope."""
    meta = extract_request_meta(params)
    business = {key: value for key, value in params.items() if key != "_meta"}
    return business, meta


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
