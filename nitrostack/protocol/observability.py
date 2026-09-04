"""W3C Trace Context extraction for MCP _meta envelopes (Doc 09 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from nitrostack.protocol.meta import MCP_META_PREFIX, RequestMeta


@dataclass(frozen=True)
class TraceContext:
    """Distributed tracing metadata propagated through MCP _meta."""

    traceparent: str | None = None
    tracestate: str | None = None
    baggage: str | None = None


def _meta_get(meta: dict[str, Any], bare: str, prefixed: str) -> Optional[str]:
    value = meta.get(prefixed)
    if value is None:
        value = meta.get(bare)
    return value if isinstance(value, str) and value.strip() else None


def extract_trace_context(meta: dict[str, Any] | None) -> TraceContext | None:
    """Extract W3C trace fields from a ``_meta`` dict (Doc 09 §2.1)."""
    if not meta:
        return None

    traceparent = _meta_get(meta, "traceparent", f"{MCP_META_PREFIX}traceparent")
    tracestate = _meta_get(meta, "tracestate", f"{MCP_META_PREFIX}tracestate")
    baggage = _meta_get(meta, "baggage", f"{MCP_META_PREFIX}baggage")

    if not traceparent and not tracestate and not baggage:
        return None

    return TraceContext(traceparent=traceparent, tracestate=tracestate, baggage=baggage)


def trace_context_from_request_meta(meta: RequestMeta) -> TraceContext | None:
    """Build ``TraceContext`` from parsed ``RequestMeta``."""
    if not meta.traceparent and not meta.tracestate and not meta.baggage:
        return None
    return TraceContext(
        traceparent=meta.traceparent,
        tracestate=meta.tracestate,
        baggage=meta.baggage,
    )
