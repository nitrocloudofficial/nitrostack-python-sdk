"""Resources implementation mixin."""

import json
import logging
import uuid
from typing import Dict, List

import mcp.types as types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import BaseModel

from nitrostack.core.app_context import _apply_request_envelope, _http_headers_from_request_ctx
from nitrostack.core.app_mode import get_widget_mime_type
from nitrostack.core.context import ExecutionContext
from nitrostack.core.errors import (
    PromptNotFoundError,
    ResourceNotFoundError,
)
from nitrostack.core.interceptors.data_spillover import (
    caller_owner,
)
from nitrostack.core.pipeline import run_pipeline
from nitrostack.runtime.request_ctx import request_ctx
from nitrostack.transports.headers import (
    extract_mcp_param_headers,
    merge_mcp_param_headers,
)
from nitrostack.widgets.mcp_meta import resource_read_contents_meta

logger = logging.getLogger(__name__)


class ResourcesMixin:
    async def _read_resource(self, uri: str) -> List[ReadResourceContents]:
        for prefix, store in self._spillover.routes.items():
            if uri.startswith(prefix):
                ctx = ExecutionContext(request_id=str(uuid.uuid4()))
                rc = request_ctx.get(None)
                _apply_request_envelope(ctx, rc)
                await self._bind_spillover(ctx, rc)
                try:
                    entry = await store.get(uri[len(prefix) :], owner=caller_owner(ctx))
                except (PermissionError, ValueError):
                    raise ResourceNotFoundError(uri) from None
                if entry is None:
                    raise ResourceNotFoundError(uri)
                return [
                    ReadResourceContents(
                        content=entry.payload.decode("utf-8"),
                        mime_type=entry.mime_type,
                    )
                ]
        entry = self._resources.get(uri)
        path_kwargs: Dict[str, str] = {}

        if entry is None:
            for template_entry in self._resource_templates:
                match = template_entry.pattern.match(uri) if template_entry.pattern else None
                if match:
                    entry = template_entry
                    path_kwargs = match.groupdict()
                    break

        if entry is None:
            raise ResourceNotFoundError(uri)

        if entry.component is not None:
            return [
                ReadResourceContents(
                    content=entry.component.get_bundle(),
                    mime_type=get_widget_mime_type(),
                    meta=resource_read_contents_meta(entry.component),
                )
            ]

        cfg = entry.config
        ctx = ExecutionContext(request_id=str(uuid.uuid4()), metadata=dict(path_kwargs))
        _apply_request_envelope(ctx, request_ctx.get(None))
        guards, middleware, interceptors, pipes, filters = self._pipeline_stages(entry.method)

        result = await run_pipeline(
            handler=entry.method,
            handler_instance=entry.instance,
            args=(),
            kwargs={**path_kwargs, "context": ctx},
            context=ctx,
            guards=guards,
            middleware=middleware,
            interceptors=interceptors,
            pipes=pipes,
            filters=filters,
        )

        # Support Discriminated Union formats for return (Section 1.2)
        if isinstance(result, dict) and "type" in result and "data" in result:
            result = result["data"]

        if isinstance(result, BaseModel):
            result = result.model_dump()

        if isinstance(result, bytes):
            return [
                ReadResourceContents(
                    content=result, mime_type=cfg.mime_type or "application/octet-stream"
                )
            ]

        if isinstance(result, str):
            return [ReadResourceContents(content=result, mime_type=cfg.mime_type or "text/plain")]

        text = json.dumps(result, indent=2, default=str)
        return [ReadResourceContents(content=text, mime_type=cfg.mime_type or "application/json")]

    async def _get_prompt(self, name: str, arguments: Dict[str, str]) -> types.GetPromptResult:
        entry = self._prompts.get(name)
        if entry is None:
            raise PromptNotFoundError(name)

        cfg = entry.config
        args_dict = dict(arguments or {})
        rc = request_ctx.get(None)
        allowed = {arg.name for arg in cfg.arguments} if cfg.arguments else None
        args_dict = merge_mcp_param_headers(
            args_dict,
            extract_mcp_param_headers(_http_headers_from_request_ctx(rc)),
            allowed_fields=allowed,
        )
        ctx = ExecutionContext(request_id=str(uuid.uuid4()), metadata=args_dict)
        _apply_request_envelope(ctx, rc)
        guards, middleware, interceptors, pipes, filters = self._pipeline_stages(entry.method)

        raw_messages = await run_pipeline(
            handler=entry.method,
            handler_instance=entry.instance,
            args=(args_dict, ctx),
            kwargs={},
            context=ctx,
            guards=guards,
            middleware=middleware,
            interceptors=interceptors,
            pipes=pipes,
            filters=filters,
        )

        if not isinstance(raw_messages, (list, tuple)):
            raw_messages = [raw_messages]

        messages: List[types.PromptMessage] = []
        for msg in raw_messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role")
            content = msg.content if hasattr(msg, "content") else msg.get("content")
            messages.append(
                types.PromptMessage(role=role, content=types.TextContent(type="text", text=content))
            )

        return types.GetPromptResult(description=cfg.description, messages=messages)
