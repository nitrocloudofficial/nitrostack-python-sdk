"""Tool-result spillover, bounded previews, and trusted caller ownership."""

from __future__ import annotations

import hashlib
import json
import uuid
import weakref
from dataclasses import dataclass
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

import mcp.types as types
from pydantic import BaseModel

from nitrostack.auth.request import authorization_token_from_request, tenant_from_claims
from nitrostack.core.context import ExecutionContext
from nitrostack.protocol.mrtr import InputRequiredResult
from nitrostack.core.interceptors.spillover import MemorySpilloverStore, SpilloverStore
from nitrostack.protocol.version import wire_mode_for_era
from nitrostack.runtime.caller import session_id_from_request_context
from nitrostack.runtime.request_ctx import RequestContext, stdio_connection_id


def generate_preview(value: Any, *, max_bytes: int = 2048) -> dict:
    """Include head items and counts, shrinking even deeply nested values to fit."""
    if max_bytes < 128:
        raise ValueError("Preview budget must be at least 128 bytes")
    if isinstance(value, list):
        preview = {"type": "list", "count": len(value), "head": value[:5], "truncated": len(value) > 5}
    elif isinstance(value, dict):
        preview = {"type": "dict", "count": len(value), "head": dict(islice(value.items(), 5)),
                   "truncated": len(value) > 5}
    else:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        preview = {"type": "string", "count": len(text), "text": text[:512], "truncated": len(text) > 512}
    encoded = json.dumps(preview, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > max_bytes:
        text = json.dumps(preview.get("head", preview.get("text")), ensure_ascii=False, default=str)
        preview.pop("head", None)
        preview["text"] = text[:512]
        preview["truncated"] = True
        while len(json.dumps(preview, ensure_ascii=False, default=str).encode("utf-8")) > max_bytes:
            preview["text"] = preview["text"][:len(preview["text"]) // 2]
    return preview


@dataclass(frozen=True)
class SpilloverResult:
    preview: dict
    resource_uri: str
    original_size_bytes: int

    def to_mcp(self) -> types.CallToolResult:
        data = dict(preview=self.preview, resource_uri=self.resource_uri,
                    original_size_bytes=self.original_size_bytes)
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str)),
                types.ResourceLink(type="resource_link", uri=self.resource_uri,
                                   name="Full tool result"),
            ],
            structuredContent=data, isError=False,
        )


SPILLOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "preview": {"type": "object"},
        "resource_uri": {"type": "string"},
        "original_size_bytes": {"type": "integer", "minimum": 0},
    },
    "required": ["preview", "resource_uri", "original_size_bytes"],
}


class SpilloverRegistry:
    """Per-application routes and weakly held anonymous session identities."""

    def __init__(self):
        self.routes: dict[str, SpilloverStore] = {}
        self._sessions: dict[int, tuple[weakref.ref, str]] = {}

    def register(self, interceptor):
        prefix, store = interceptor.resource_uri_prefix, interceptor.store
        for existing, registered in self.routes.items():
            if prefix.startswith(existing) or existing.startswith(prefix):
                if prefix != existing or store is not registered:
                    raise ValueError("Conflicting spillover resource prefixes")
        self.routes[prefix] = store

    def session_key(self, rc, era=None):
        request = getattr(rc, "request", None)
        if getattr(request, "scope", {}).get("type") == "http":
            # Only routed requests reach here: the official manager has validated
            # this ID. SDK session proxies themselves are per-request objects.
            if wire_mode_for_era(era or "auto") != "sessionful":
                return None
            session_id = session_id_from_request_context(rc)
            return "http:" + session_id if session_id else None
        connection_id = stdio_connection_id.get()
        if connection_id is not None:
            return "stdio:" + connection_id
        # Explicit in-process contexts retain their weakly-held test/session key.
        # Never infer ownership from an official SDK request's proxy identity.
        if not isinstance(rc, RequestContext):
            return None
        session = getattr(rc, "session", None)
        if session is None:
            return None
        identity = id(session)
        existing = self._sessions.get(identity)
        if existing is not None and existing[0]() is session:
            return existing[1]
        key = "in-process:" + uuid.uuid4().hex
        try:
            reference = weakref.ref(session, lambda _: self._sessions.pop(identity, None))
        except TypeError:
            return None
        self._sessions[identity] = (reference, key)
        return key


def caller_owner(context: ExecutionContext) -> str:
    auth = context.auth
    if auth is not None and (auth.subject or auth.client_id):
        identity = ["principal", auth.iss, auth.subject, auth.client_id,
                    tenant_from_claims(auth.claims)]
    elif context.spillover_invalid_auth:
        raise PermissionError("Verified identity required for spillover")
    elif context.spillover_session_key:
        identity = ["session", context.spillover_session_key]
    else:
        raise PermissionError("Spillover requires a verified caller or trusted MCP session")
    return hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()


def bind_spillover_context(context, rc, registry, era=None):
    context.spillover_session_key = registry.session_key(rc, era)
    context.spillover_invalid_auth = bool(
        authorization_token_from_request(rc) and context.auth is None
    )


class DataSpilloverInterceptor:
    def __init__(
        self, threshold_bytes: int = 64 * 1024, store: SpilloverStore | None = None,
        resource_uri_prefix: str = "resource://data-spillover/",
    ):
        if type(threshold_bytes) is not int or threshold_bytes < 0:
            raise ValueError("Spillover threshold must be a nonnegative integer")
        parts = urlsplit(resource_uri_prefix)
        if (not parts.scheme or not parts.netloc or parts.query or parts.fragment
                or not resource_uri_prefix.endswith("/")):
            raise ValueError("Resource prefix must be an absolute URI ending in '/'")
        self.threshold_bytes = threshold_bytes
        self.store = store if store is not None else MemorySpilloverStore()
        self.resource_uri_prefix = resource_uri_prefix

    async def intercept(self, context, next_fn):
        result = await next_fn()
        if isinstance(result, (InputRequiredResult, SpilloverResult)):
            return result
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            return result
        value = result
        if isinstance(value, types.CallToolResult):
            if value.is_error or value.result_type == "input_required":
                return result
            value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
        elif isinstance(value, dict) and value.get("isError"):
            return result
        elif isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, str):
            payload, mime = value.encode("utf-8"), "text/plain"
        else:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
            mime = "application/json"
        if len(payload) <= self.threshold_bytes:
            return result
        owner = caller_owner(context)
        key = await self.store.put(payload, owner=owner, mime_type=mime)
        return SpilloverResult(generate_preview(value), self.resource_uri_prefix + key, len(payload))
