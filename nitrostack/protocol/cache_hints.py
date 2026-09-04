"""Cache hint resolution for tools, resources, and list endpoints (Doc 09 §3)."""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from nitrostack.protocol.contracts import MCP_CACHE_HINT_KEY, build_cache_hint_meta

DEFAULT_LIST_CACHE_TTL_MS = 60_000


def resolve_tool_cache_hint_meta(
    tool_config: Any,
    method: Optional[Callable] = None,
) -> Optional[dict[str, Any]]:
    """
    Resolve tool ``_meta`` cache hints (Doc 09 §3.2).

    Priority: explicit ``metadata['cacheHint']`` / wire key, then ``@cache(ttl=...)``.
    """
    metadata = getattr(tool_config, "metadata", None) or {}
    explicit = metadata.get("cacheHint") or metadata.get(MCP_CACHE_HINT_KEY)
    if isinstance(explicit, dict):
        ttl_ms = explicit.get("ttlMs")
        scope = explicit.get("cacheScope", "private")
        if isinstance(ttl_ms, int) and ttl_ms >= 0:
            return build_cache_hint_meta(ttl_ms, cache_scope=scope if scope in ("public", "private") else "private")

    cache_hint = getattr(tool_config, "cache_hint", None)
    if isinstance(cache_hint, dict):
        ttl_ms = cache_hint.get("ttlMs")
        scope = cache_hint.get("cacheScope", "private")
        if isinstance(ttl_ms, int) and ttl_ms >= 0:
            return build_cache_hint_meta(ttl_ms, cache_scope=scope if scope in ("public", "private") else "private")

    if method is not None:
        ttl_seconds = getattr(method, "_mcp_cache_ttl", None)
        if isinstance(ttl_seconds, (int, float)) and ttl_seconds >= 0:
            scope = getattr(method, "_mcp_cache_scope", "private")
            return build_cache_hint_meta(
                int(ttl_seconds * 1000),
                cache_scope=scope if scope in ("public", "private") else "private",
            )

    return None


def resolve_resource_cache_hint_meta(resource_config: Any) -> Optional[dict[str, Any]]:
    """Resolve resource cache hints from ``cacheHint`` or ``cacheMaxAge`` metadata."""
    metadata = getattr(resource_config, "metadata", None) or {}
    explicit = metadata.get("cacheHint") or metadata.get(MCP_CACHE_HINT_KEY)
    if isinstance(explicit, dict):
        ttl_ms = explicit.get("ttlMs")
        scope = explicit.get("cacheScope", "private")
        if isinstance(ttl_ms, int) and ttl_ms >= 0:
            return build_cache_hint_meta(ttl_ms, cache_scope=scope if scope in ("public", "private") else "private")

    cache_max_age = metadata.get("cacheMaxAge")
    if isinstance(cache_max_age, (int, float)) and cache_max_age >= 0:
        return build_cache_hint_meta(int(cache_max_age * 1000), cache_scope="private")

    cache_hint = getattr(resource_config, "cache_hint", None)
    if isinstance(cache_hint, dict):
        ttl_ms = cache_hint.get("ttlMs")
        scope = cache_hint.get("cacheScope", "private")
        if isinstance(ttl_ms, int) and ttl_ms >= 0:
            return build_cache_hint_meta(ttl_ms, cache_scope=scope if scope in ("public", "private") else "private")

    return None


def build_list_endpoint_cache_hint_meta(
    *,
    ttl_ms: int = DEFAULT_LIST_CACHE_TTL_MS,
    cache_scope: Literal["public", "private"] = "private",
) -> dict[str, Any]:
    """Cache hint for ``tools/list``, ``resources/list``, and ``prompts/list`` responses."""
    return build_cache_hint_meta(ttl_ms, cache_scope=cache_scope)
