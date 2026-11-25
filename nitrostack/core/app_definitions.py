"""Definitions implementation mixin."""

import logging
from typing import Any, Dict, Type

import mcp.types as types
from pydantic import BaseModel

from nitrostack.core.app_entries import _PromptEntry, _ResourceEntry, _ToolEntry
from nitrostack.core.app_mode import is_openai_mode
from nitrostack.core.app_schema import inspector_friendly_schema, tool_json_schema
from nitrostack.core.interceptors.data_spillover import (
    SPILLOVER_SCHEMA,
)
from nitrostack.protocol.cache_hints import (
    resolve_resource_cache_hint_meta,
    resolve_tool_cache_hint_meta,
)
from nitrostack.protocol.schema import (
    normalize_input_schema,
)
from nitrostack.widgets.mcp_meta import build_tool_list_meta

logger = logging.getLogger(__name__)


class DefinitionsMixin:
    def _tool_input_schema(self, input_model: Type[BaseModel]) -> Dict[str, Any]:
        """JSON Schema for `tools/list` with fields at the top level.

        MCP Inspector renders `inputSchema.properties` as form fields. Nesting
        the model under `properties.input.$ref` only showed labels.
        """
        schema = inspector_friendly_schema(input_model.model_json_schema())
        if not isinstance(schema, dict):
            schema = {}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return normalize_input_schema(schema)

    def _build_tool_definition(self, entry: _ToolEntry) -> types.Tool:
        cfg = entry.config
        input_schema = self._tool_input_schema(entry.input_model)

        tasks_visible = self._tasks_visible_on_wire()
        meta: Dict[str, Any] = {
            "is_initial": cfg.is_initial,
            "visibility": cfg.visibility,
            **({"task_support": cfg.task_support} if tasks_visible else {}),
            **(cfg.metadata or {}),
        }

        if entry.component is not None:
            widget_meta = build_tool_list_meta(
                entry.component,
                cfg.visibility,
                cfg.invocation,
            )
            meta.update(widget_meta)
        elif cfg.invocation and is_openai_mode():
            meta["openai/toolInvocation/invoking"] = cfg.invocation.invoking
            meta["openai/toolInvocation/invoked"] = cfg.invocation.invoked

        if cfg.examples:
            meta["examples"] = {
                "input": cfg.examples.input,
                "output": cfg.examples.output,
                "description": cfg.examples.description,
            }

        cache_meta = resolve_tool_cache_hint_meta(cfg, entry.method)
        if cache_meta:
            meta.update(cache_meta)

        if is_openai_mode():
            meta["openai/type"] = "function"
            meta["openai/function"] = {
                "name": cfg.name,
                "description": cfg.description,
                "parameters": input_schema,
            }

        annotations = types.ToolAnnotations(
            readOnlyHint=cfg.annotations.read_only_hint,
            destructiveHint=cfg.annotations.destructive_hint,
            idempotentHint=cfg.annotations.idempotent_hint,
            openWorldHint=cfg.annotations.open_world_hint,
        )

        execution = None
        if tasks_visible and cfg.task_support and cfg.task_support != "forbidden":
            execution = types.ToolExecution(taskSupport=cfg.task_support)

        tool_kwargs: Dict[str, Any] = {
            "name": cfg.name,
            "title": cfg.title,
            "description": cfg.description,
            "inputSchema": input_schema,
            "annotations": annotations,
            "execution": execution,
            "_meta": meta,
        }
        if entry.component is not None and is_openai_mode():
            tool_kwargs["outputTemplate"] = entry.component.resource_uri

        output_schema = tool_json_schema(cfg.output_schema)
        if output_schema is not None:
            if self._has_spillover(entry.method):
                output_schema = {
                    "type": "object",
                    "anyOf": [output_schema, SPILLOVER_SCHEMA],
                    **({"$defs": output_schema["$defs"]} if "$defs" in output_schema else {}),
                }
            tool_kwargs["outputSchema"] = output_schema

        return types.Tool(**tool_kwargs)

    def _build_resource_definition(self, entry: _ResourceEntry) -> types.Resource:
        cfg = entry.config
        meta = dict(cfg.metadata or {})
        cache_meta = resolve_resource_cache_hint_meta(cfg)
        if cache_meta:
            meta.update(cache_meta)
        resource_kwargs: Dict[str, Any] = {
            "uri": cfg.uri,
            "name": cfg.name,
            "title": cfg.title,
            "description": cfg.description,
            "mimeType": cfg.mime_type,
            "size": cfg.size,
        }
        if meta:
            resource_kwargs["_meta"] = meta
        return types.Resource(**resource_kwargs)

    def _build_resource_template_definition(self, entry: _ResourceEntry) -> types.ResourceTemplate:
        cfg = entry.config
        return types.ResourceTemplate(
            uriTemplate=cfg.uri,
            name=cfg.name,
            title=cfg.title,
            description=cfg.description,
            mimeType=cfg.mime_type,
        )

    def _build_prompt_definition(self, entry: _PromptEntry) -> types.Prompt:
        cfg = entry.config
        arguments = [
            types.PromptArgument(name=arg.name, description=arg.description, required=arg.required)
            for arg in cfg.arguments
        ]
        return types.Prompt(name=cfg.name, description=cfg.description, arguments=arguments or None)
