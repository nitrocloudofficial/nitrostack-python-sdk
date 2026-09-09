"""Wire contract builders for tools, resources, and prompts."""

from __future__ import annotations

from typing import Any, Literal, Optional

MCP_CACHE_HINT_KEY = "io.modelcontextprotocol/cacheHint"


def build_cache_hint_meta(
    ttl_ms: int,
    *,
    cache_scope: Literal["public", "private"] = "private",
) -> dict[str, Any]:
    """Build tool/resource ``_meta`` cache hint (SEP-2549)."""
    return {
        MCP_CACHE_HINT_KEY: {
            "ttlMs": ttl_ms,
            "cacheScope": cache_scope,
        }
    }


def merge_contract_meta(*parts: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple ``_meta`` dicts for wire definitions."""
    merged: dict[str, Any] = {}
    for part in parts:
        if part:
            merged.update(part)
    return merged


def build_resource_text_content(
    uri: str,
    text: str,
    *,
    mime_type: str = "application/json",
) -> dict[str, Any]:
    """Build a text ``resources/read`` content item."""
    return {
        "uri": uri,
        "mimeType": mime_type,
        "text": text,
    }


def build_resource_blob_content(
    uri: str,
    blob: str,
    *,
    mime_type: str = "application/octet-stream",
) -> dict[str, Any]:
    """Build a base64 blob ``resources/read`` content item."""
    return {
        "uri": uri,
        "mimeType": mime_type,
        "blob": blob,
    }


def build_prompt_text_message(role: str, text: str) -> dict[str, Any]:
    """Build a single prompt message entry."""
    return {
        "role": role,
        "content": {
            "type": "text",
            "text": text,
        },
    }


def build_prompt_get_result(
    description: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the ``prompts/get`` result payload."""
    return {
        "description": description,
        "messages": messages,
    }
