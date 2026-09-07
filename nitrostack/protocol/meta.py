"""Request _meta envelope parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

MCP_META_PREFIX = "io.modelcontextprotocol/"


@dataclass(frozen=True)
class RequestMeta:
    """Parsed _meta envelope lifted into execution context."""

    protocol_version: Optional[str] = None
    client_info: Optional[dict[str, Any]] = None
    client_capabilities: Optional[dict[str, Any]] = None
    traceparent: Optional[str] = None
    tracestate: Optional[str] = None
    baggage: Optional[str] = None
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

    return RequestMeta(
        protocol_version=protocol_version if isinstance(protocol_version, str) else None,
        client_info=client_info if isinstance(client_info, dict) else None,
        client_capabilities=client_capabilities if isinstance(client_capabilities, dict) else None,
        traceparent=traceparent if isinstance(traceparent, str) else None,
        tracestate=tracestate if isinstance(tracestate, str) else None,
        baggage=baggage if isinstance(baggage, str) else None,
        raw=dict(raw),
    )


def split_params_and_meta(params: dict[str, Any]) -> tuple[dict[str, Any], RequestMeta]:
    """Separate business params from the _meta envelope."""
    meta = extract_request_meta(params)
    business = {key: value for key, value in params.items() if key != "_meta"}
    return business, meta
