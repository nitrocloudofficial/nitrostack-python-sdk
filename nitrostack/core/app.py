import os
import sys
import re
import json
import uuid
import asyncio
import inspect
import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Pattern, Set, Tuple, Type

import mcp.types as types
from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    GetPromptRequestParams,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    SubscribeRequestParams,
    UnsubscribeRequestParams,
)
from pydantic import BaseModel, create_model

from nitrostack.core.context import ExecutionContext, TaskContext
from nitrostack.core.decorators import ToolConfig, ResourceConfig, PromptConfig, widget_resource_uri
from nitrostack.core.app_mode import get_app_mode, get_widget_mime_type, is_mcp_app_mode, is_openai_mode
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import (
    DependencyResolutionError,
    PromptNotFoundError,
    ResourceNotFoundError,
    TaskAlreadyTerminalError,
    TaskExpiredError,
    TaskNotFoundError,
)
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.core.pipeline import run_pipeline
from nitrostack.core.additional_decorators import HealthCheckRegistry
from nitrostack.core.task import TaskManager, TaskStatus
from nitrostack.events.event_emitter import EventEmitter
from nitrostack.protocol.schema import (
    gate_registered_schema,
    normalize_input_schema,
    normalize_output_schema,
)
from nitrostack.protocol.resources import extract_template_param_names, uri_template_to_pattern
from nitrostack.protocol.version import (
    MODERN_PROTOCOL_VERSION,
    EraSource,
    ProtocolEra,
    protocol_version_for_era,
    resolve_http_engine,
    resolve_protocol_era_resolution,
    wire_mode_for_era,
)
from nitrostack.protocol.mrtr import InputRequiredResult, split_mrtr_from_arguments
from nitrostack.protocol.cache_hints import (
    build_list_endpoint_cache_hint_meta,
    resolve_resource_cache_hint_meta,
    resolve_tool_cache_hint_meta,
)
from nitrostack.auth.request import (
    auth_context_from_request,
    bearer_token_from_envelope_auth,
    envelope_auth_slot,
)
from nitrostack.protocol.meta import (
    bind_request_envelope,
    flatten_request_meta_object,
    strip_tool_arguments,
)
from nitrostack.protocol.observability import TraceContext, extract_trace_context
from nitrostack.runtime.correlation import InFlightRegistry, new_correlation_id
from nitrostack.transports.headers import (
    extract_mcp_param_headers,
    extract_mcp_scope_headers,
    merge_mcp_param_headers,
)
from nitrostack.protocol.deprecated import (
    deprecated_method_message,
    rejects_deprecated_method,
)
from nitrostack.protocol.tasks import (
    DEFAULT_TASK_TTL_MS,
    task_support_forbidden_message,
    task_support_required_message,
)
from nitrostack.runtime.request_ctx import ServerResult, request_ctx
from nitrostack.tasks.authorization import extract_task_access_context
from nitrostack.widgets.component import Component, find_project_root, load_widget_html, parse_widget_options
from nitrostack.widgets.mcp_meta import build_call_tool_result_meta, build_tool_list_meta, resource_read_contents_meta
from nitrostack.widgets.route_templates import build_missing_widget


DEFAULT_HTTP_PORT = 3000
logger = logging.getLogger(__name__)


def _iso_timestamp(value: Any) -> str:
    """Official Task timestamps are ISO-8601 strings."""
    if hasattr(value, "isoformat"):
        text = value.isoformat()
        if text.endswith("+00:00"):
            return text[:-6] + "Z"
        return text
    return str(value)


class GetTaskResult(types.GetTaskResult):
    """Official GetTaskResult plus NitroStack-owned ``result`` / ``error`` slots."""

    result: Any = None
    error: Any = None


def resolve_http_port() -> int:
    """MCP HTTP/dual bind port. Defaults to 3000; 3001 is reserved for widgets."""
    return int(os.environ.get("PORT") or os.environ.get("MCP_SERVER_PORT") or DEFAULT_HTTP_PORT)


def resolve_http_host() -> str:
    """Bind host. ``HOST`` matches the TypeScript templates; default is ``127.0.0.1``."""
    return (os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1"


@dataclass
class ServerConfig:
    name: str
    version: str = "1.0.0"
    transport_type: Optional[Literal["stdio", "http", "dual"]] = None
    protocol_version: str = MODERN_PROTOCOL_VERSION
    # Era fallback when MCP_STATELESS and NITRO_MCP_PROTOCOL_VERSION are unset.
    # Same tokens as the env var (modern/latest/2026/2026-07-28, auto/both/dual/
    # dual-spec, legacy/2025/2025-06-18/2025-11-25). None means default era (`auto`).
    protocol_era: Optional[str] = None
    # Streamable HTTP options. Era resolution at `start()` / `get_combined_app()`:
    # `MCP_STATELESS`, then `NITRO_MCP_PROTOCOL_VERSION`, then `protocol_era`,
    # then `auto`. Also: `ENABLE_CORS`, `MCP_MAX_SESSIONS`,
    # `MCP_SESSION_TIMEOUT_MS`, `MCP_TRANSPORT_TYPE`.
    # `MCP_STATELESS` wins over the protocol-era mapping. Unset era is `auto`
    # and does not force this flag; only `modern` sets stateless HTTP.
    stateless: bool = False
    max_sessions: Optional[int] = None
    session_timeout_ms: Optional[int] = None
    json_response: bool = False
    extensions: Optional[Dict[str, str]] = None


def mcp_app(module: Type, server: ServerConfig):
    """
    Decorator to declare the main application class.
    Specifies the root AppModule and ServerConfig.
    """
    def decorator(cls: Type):
        cls._mcp_app_module = module
        cls._mcp_app_server = server
        return cls
    return decorator


def get_pydantic_model(schema: Any) -> Type[BaseModel]:
    """Helper to resolve or construct a Pydantic model for input validation."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    if isinstance(schema, dict):
        # Dynamically build a Pydantic model from JSON schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        fields = {}
        for name, prop in properties.items():
            t = Any
            prop_type = prop.get("type")
            if prop_type == "string":
                t = str
            elif prop_type == "integer":
                t = int
            elif prop_type == "number":
                t = float
            elif prop_type == "boolean":
                t = bool
            elif prop_type == "array":
                t = list
            elif prop_type == "object":
                t = dict

            default = ... if name in required else None
            fields[name] = (t, default)
        return create_model("DynamicInputModel", **fields)

    # Return a default empty model if invalid or empty
    return create_model("EmptyInputModel")


def _is_null_schema(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _unwrap_nullable_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Turn JSON Schema `T | null` into `T` so MCP Inspector can render widgets.

    Pydantic v2 emits Optional fields as `anyOf: [{type: T}, {type: null}]` (or
    `type: [T, "null"]`). Inspector/RJSF shows the property name but no input
    control for those unions.
    """
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if not isinstance(variants, list) or len(variants) != 2:
            continue
        non_null = [item for item in variants if not _is_null_schema(item)]
        if len(non_null) != 1:
            continue
        merged = dict(non_null[0])
        for extra_key, extra_val in schema.items():
            if extra_key == key:
                continue
            if extra_key not in merged:
                merged[extra_key] = extra_val
            elif extra_key in ("description", "title", "default") and extra_val is not None:
                merged[extra_key] = extra_val
        if merged.get("default") is None:
            merged.pop("default", None)
        return merged

    types = schema.get("type")
    if isinstance(types, list):
        non_null = [item for item in types if item != "null"]
        if len(non_null) == 1:
            schema = {**schema, "type": non_null[0]}
            if schema.get("default") is None:
                schema.pop("default", None)
    return schema


def inspector_friendly_schema(node: Any) -> Any:
    """Normalize a Pydantic JSON Schema so MCP Inspector can render form fields."""
    if isinstance(node, list):
        return [inspector_friendly_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    unwrapped = _unwrap_nullable_schema(node)
    return {key: inspector_friendly_schema(value) for key, value in unwrapped.items()}


def tool_json_schema(schema_spec: Any) -> Optional[Dict[str, Any]]:
    """Convert a Pydantic model class or JSON-schema dict to MCP 2026-07-28 outputSchema."""
    if schema_spec is None:
        return None
    if isinstance(schema_spec, dict):
        return normalize_output_schema(inspector_friendly_schema(schema_spec))
    model = get_pydantic_model(schema_spec)
    if model is not None:
        return normalize_output_schema(inspector_friendly_schema(model.model_json_schema()))
    return None


def _is_blank_inspector_value(value: Any) -> bool:
    """Inspector leaves unused form fields as ``""`` (or whitespace)."""
    return value is None or (isinstance(value, str) and not value.strip())


def _omit_blank_optional_fields(payload: Dict[str, Any], input_model: Type[BaseModel]) -> Dict[str, Any]:
    """Drop blank optional/defaulted fields so Pydantic defaults apply.

    MCP Inspector sends ``filter: ""`` when the enum is left empty. Zod in the
    TS SDK marks that field ``.optional()`` and then uses ``args.filter || 'all'``.
    Pydantic ``default='all'`` only runs when the key is missing, not when it is
    an empty string — so we omit blanks for non-required fields.
    """
    fields = getattr(input_model, "model_fields", {}) or {}
    cleaned: Dict[str, Any] = {}
    for key, value in payload.items():
        field = fields.get(key)
        if field is not None and (not field.is_required()) and _is_blank_inspector_value(value):
            continue
        cleaned[key] = value
    return cleaned


def parse_tool_input(input_model: Type[BaseModel], arguments: Optional[Dict[str, Any]]) -> BaseModel:
    """Validate tool arguments from Inspector or the older nested wrap.

    Inspector sends top-level fields (`{openNow: true}`).
    Older Python clients wrap them (`{input: {openNow: true}}`).
    Empty strings on optional fields are treated as omitted (Inspector dropdowns).
    """
    arguments = arguments or {}
    inner = arguments.get("input")
    looks_wrapped = (
        isinstance(inner, dict)
        and set(arguments.keys()) <= {"input"}
        and "input" not in input_model.model_fields
    )
    payload = inner if looks_wrapped else arguments
    if not isinstance(payload, dict):
        payload = {}
    return input_model.model_validate(_omit_blank_optional_fields(payload, input_model))


@dataclass
class _ToolEntry:
    config: ToolConfig
    input_model: Type[BaseModel]
    instance: Any
    method: Callable
    component: Optional[Component] = None


@dataclass
class _ResourceEntry:
    config: ResourceConfig
    instance: Any
    method: Callable
    param_names: List[str] = field(default_factory=list)
    pattern: Optional[Pattern] = None
    component: Optional[Component] = None


@dataclass
class _PromptEntry:
    config: PromptConfig
    instance: Any
    method: Callable


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


def _read_contents_to_result(uri: str, items: List[ReadResourceContents]) -> types.ReadResourceResult:
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
                types.TextResourceContents(
                    uri=uri, mimeType=mime, text=str(item.content), **extra
                )
            )
    return types.ReadResourceResult(contents=contents)


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
        return {
            "input": merge_mcp_param_headers(
                inner, param_headers, allowed_fields=allowed
            )
        }
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

class McpApplication:
    def __init__(self, app_class: Type):
        self.app_class = app_class
        if hasattr(app_class, "_mcp_app_module"):
            self.root_module = app_class._mcp_app_module
            self.server_config = app_class._mcp_app_server
        elif hasattr(app_class, "_mcp_module_config"):
            self.root_module = app_class
            self.server_config = ServerConfig(name=app_class._mcp_module_config.name or "mcp-server")
        else:
            raise ValueError("Invalid application class. Must be decorated with @mcp_app or @module.")

        self.mcp_server: Optional[NitroStackMcpServer] = None
        era_resolution = resolve_protocol_era_resolution(
            config_value=self.server_config.protocol_era
        )
        self.protocol_era: ProtocolEra = era_resolution.era
        self.protocol_era_source: EraSource = era_resolution.source

        # nitrostack owns these registries so any number of low-level `Server`
        # instances can be wired against the same tools/resources/prompts (see
        # `create_configured_mcp_server`).
        self._tools: Dict[str, _ToolEntry] = {}
        self._resources: Dict[str, _ResourceEntry] = {}
        self._resource_templates: List[_ResourceEntry] = []
        self._prompts: Dict[str, _PromptEntry] = {}
        self._initial_tools: List[Tuple[Any, Callable, ToolConfig]] = []
        self.task_manager = TaskManager()
        self._in_flight = InFlightRegistry()

        self._bootstrap()

    def _bootstrap(self) -> None:
        # 1. Construct the low-level server directly.
        self.mcp_server = NitroStackMcpServer(
            name=self.server_config.name,
            version=self.server_config.version,
        )

        # 2. Resolve Module Tree recursively and register services
        resolved_modules: Set[Type] = set()
        self._resolve_modules(self.root_module, resolved_modules)

        container = DIContainer.get_instance()
        self._assert_declared_dependencies(resolved_modules, container)

        # Instantiate all providers and controllers to populate container.
        # Scan both: providers with @tool/@resource/@prompt must stay discoverable.
        module_instances: List[Any] = []
        seen_instance_ids: Set[int] = set()
        for mod in resolved_modules:
            mod_config = getattr(mod, "_mcp_module_config", None)
            if mod_config:
                for provider in mod_config.providers:
                    instance = container.resolve(provider)
                    if id(instance) not in seen_instance_ids:
                        seen_instance_ids.add(id(instance))
                        module_instances.append(instance)
                for controller in mod_config.controllers:
                    instance = container.resolve(controller)
                    if id(instance) not in seen_instance_ids:
                        seen_instance_ids.add(id(instance))
                        module_instances.append(instance)

        # 3. Discover decorated methods on resolved module providers and controllers
        for instance in module_instances:
            # Scan members of this instance
            for name, member in inspect.getmembers(instance):
                # Discover Tools
                if hasattr(member, "_mcp_tool_config"):
                    tool_config: ToolConfig = getattr(member, "_mcp_tool_config")
                    self._register_tool(instance, member, tool_config)

                # Discover Resources
                if hasattr(member, "_mcp_resource_config"):
                    resource_config: ResourceConfig = getattr(member, "_mcp_resource_config")
                    self._register_resource(instance, member, resource_config)

                # Discover Prompts
                if hasattr(member, "_mcp_prompt_config"):
                    prompt_config: PromptConfig = getattr(member, "_mcp_prompt_config")
                    self._register_prompt(instance, member, prompt_config)

                # Bind Health Checks
                if hasattr(member, "_mcp_health_check_name"):
                    check_name = getattr(member, "_mcp_health_check_name")
                    HealthCheckRegistry.bind_instance(check_name, member, instance)

                # Bind Event Listeners
                if hasattr(member, "_mcp_event_name"):
                    event_name = getattr(member, "_mcp_event_name")
                    EventEmitter.get_instance().bind_instance(event_name, member, instance)

        # 4. Register Built-in Health check Resource if any checks exist
        if HealthCheckRegistry.get_checks():
            self._register_health_resource()

        # 5. Wire every protocol handler (tools/resources/prompts/tasks/notifications)
        #    onto the owned low-level server. This is factored out so additional server
        #    instances (e.g. one per HTTP session) can be configured identically.
        self._setup_handlers(self.mcp_server)

    def _assert_declared_dependencies(
        self, resolved_modules: Set[Type], container: DIContainer
    ) -> None:
        """Fail at bootstrap when a string ``deps=[...]`` token was never registered."""
        missing: List[str] = []
        seen: Set[Type] = set()

        def walk(cls: Any) -> None:
            if not isinstance(cls, type) or cls in seen:
                return
            seen.add(cls)
            for dep in getattr(cls, "_mcp_deps", []) or []:
                if isinstance(dep, str):
                    if not container.has_value(dep) and dep not in container._registry:
                        missing.append(f"{cls.__name__} -> '{dep}'")
                elif isinstance(dep, type):
                    walk(dep)

        for mod in resolved_modules:
            mod_config = getattr(mod, "_mcp_module_config", None)
            if not mod_config:
                continue
            for cls in [*mod_config.providers, *mod_config.controllers]:
                walk(cls)

        if missing:
            raise DependencyResolutionError(
                "Missing dependency at app bootstrap (referenced in deps=[...] "
                "but never registered): " + "; ".join(missing)
            )

    def _resolve_modules(self, module_class: Type, resolved_modules: Set[Type]) -> None:
        if module_class in resolved_modules:
            return
        resolved_modules.add(module_class)

        mod_config = getattr(module_class, "_mcp_module_config", None)
        if mod_config:
            for imp in mod_config.imports:
                self._resolve_modules(imp, resolved_modules)

    # ------------------------------------------------------------------
    # Registration (pure bookkeeping — no protocol/server calls here)
    # ------------------------------------------------------------------

    def _register_tool(self, instance: Any, method: Callable, tool_config: ToolConfig) -> None:
        gate_registered_schema(
            tool_config.input_schema, name=f"tool {tool_config.name!r} input"
        )
        gate_registered_schema(
            tool_config.output_schema, name=f"tool {tool_config.name!r} output"
        )
        input_model = get_pydantic_model(tool_config.input_schema)
        entry = _ToolEntry(config=tool_config, input_model=input_model, instance=instance, method=method)

        widget_spec = getattr(method, "_mcp_widget", None)
        if widget_spec is not None:
            options = parse_widget_options(widget_spec)
            resource_uri = widget_resource_uri(options.route)
            route_id = resource_uri.removeprefix("ui://widget/").removesuffix(".html")
            from_file = None
            method_module = inspect.getmodule(method)
            if method_module and getattr(method_module, "__file__", None):
                from_file = Path(method_module.__file__).resolve()
            project_root = find_project_root(from_file)
            html = load_widget_html(
                route_id,
                html=options.html,
                from_file=from_file,
                project_root=project_root,
            )
            if not html:
                html = build_missing_widget(route_id)
            component = Component(
                id=route_id,
                name=tool_config.title or tool_config.name,
                html=html,
                description=options.description or tool_config.description,
                css=options.css,
                js=options.js,
                csp=options.csp,
                domain=options.domain,
                prefers_border=options.prefers_border,
                can_invoke_tools=options.can_invoke_tools,
            )
            entry.component = component
            self._register_widget_resource(component)

        self._tools[tool_config.name] = entry
        if tool_config.is_initial:
            self._initial_tools.append((instance, method, tool_config))

    def _register_widget_resource(self, component: Component) -> None:
        config = ResourceConfig(
            uri=component.resource_uri,
            name=component.name,
            description=component.description or f"UI widget for {component.name}",
            mime_type=get_widget_mime_type(),
        )

        async def widget_resource_handler(context: ExecutionContext) -> str:
            return component.get_bundle()

        self._resources[config.uri] = _ResourceEntry(
            config=config,
            instance=None,
            method=widget_resource_handler,
            component=component,
        )

    def _register_resource(self, instance: Any, method: Callable, resource_config: ResourceConfig) -> None:
        gate_registered_schema(
            getattr(resource_config, "schema", None)
            or (resource_config.metadata or {}).get("schema"),
            name=f"resource {resource_config.uri!r}",
        )
        param_names = extract_template_param_names(resource_config.uri)
        entry = _ResourceEntry(config=resource_config, instance=instance, method=method, param_names=param_names)

        if param_names:
            entry.pattern = uri_template_to_pattern(resource_config.uri)
            self._resource_templates.append(entry)
        else:
            self._resources[resource_config.uri] = entry

    def _register_prompt(self, instance: Any, method: Callable, prompt_config: PromptConfig) -> None:
        for argument in prompt_config.arguments or []:
            gate_registered_schema(
                getattr(argument, "schema", None),
                name=f"prompt {prompt_config.name!r} argument {argument.name!r}",
            )
        self._prompts[prompt_config.name] = _PromptEntry(config=prompt_config, instance=instance, method=method)

    def _register_health_resource(self) -> None:
        config = ResourceConfig(
            uri="health://status",
            name="Health Status",
            description="System health status check",
        )

        async def health_status_resource(context: ExecutionContext) -> str:
            results = HealthCheckRegistry.run_all()
            return json.dumps(results)

        self._resources[config.uri] = _ResourceEntry(config=config, instance=None, method=health_status_resource)

    # ------------------------------------------------------------------
    # Protocol handler wiring (owned low-level `mcp.server.lowlevel.Server`)
    # ------------------------------------------------------------------

    def _advertise_tasks_extension(self) -> bool:
        return any(
            entry.config.task_support in ("optional", "required")
            for entry in self._tools.values()
        )

    def _custom_extensions(self) -> Optional[Dict[str, str]]:
        extensions = getattr(self.server_config, "extensions", None)
        if not extensions:
            return None
        return dict(extensions)

    def handle_server_discover(self, protocol_version: Optional[str] = None) -> Dict[str, Any]:
        """Single ``server/discover`` result for the mounted HTTP engine."""
        from nitrostack.protocol.discovery import build_discover_result

        has_widgets = any(
            getattr(entry, "component", None) is not None
            for entry in getattr(self, "_tools", {}).values()
        )
        version = protocol_version or protocol_version_for_era(
            getattr(self, "protocol_era", None),
            self.server_config.protocol_version,
        )
        return build_discover_result(
            server_name=self.server_config.name,
            server_version=self.server_config.version,
            protocol_version=version,
            advertise_tasks=self._advertise_tasks_extension(),
            advertise_app=has_widgets,
            custom_extensions=self._custom_extensions(),
        )

    def handle_sessionless_initialize(self, requested_version: Optional[str] = None) -> Dict[str, Any]:
        """Answer 2025 ``initialize`` on the sessionless ``auto`` path. No session."""
        from nitrostack.protocol.discovery import build_sessionless_initialize_result

        has_widgets = any(
            getattr(entry, "component", None) is not None
            for entry in getattr(self, "_tools", {}).values()
        )
        version = protocol_version_for_era(
            getattr(self, "protocol_era", None),
            self.server_config.protocol_version,
        )
        return build_sessionless_initialize_result(
            server_name=self.server_config.name,
            server_version=self.server_config.version,
            requested_version=requested_version,
            protocol_version=version,
            advertise_tasks=self._advertise_tasks_extension(),
            advertise_app=has_widgets,
            custom_extensions=self._custom_extensions(),
        )

    def _list_endpoint_cache_meta(self) -> Dict[str, Any]:
        return build_list_endpoint_cache_hint_meta()

    def _setup_handlers(self, server: NitroStackMcpServer) -> None:
        async def _list_tools(_ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]):
            return types.ListToolsResult(
                tools=[self._build_tool_definition(entry) for entry in self._tools.values()],
                _meta=self._list_endpoint_cache_meta(),
            )

        async def _call_tool(ctx: ServerRequestContext, params: CallToolRequestParams):
            token = request_ctx.set(ctx)
            try:
                result = await self._call_tool(
                    params.name, params.arguments or {}, task=params.task
                )
                if isinstance(result, types.CreateTaskResult):
                    task = result.task
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=task.task_id)],
                        structuredContent={
                            "resultType": "task",
                            "task": task.model_dump(by_alias=True),
                        },
                        isError=False,
                    )
                return result
            finally:
                request_ctx.reset(token)

        async def _list_resources(_ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]):
            return types.ListResourcesResult(
                resources=[self._build_resource_definition(entry) for entry in self._resources.values()],
                _meta=self._list_endpoint_cache_meta(),
            )

        async def _list_resource_templates(_ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]):
            return types.ListResourceTemplatesResult(
                resource_templates=[
                    self._build_resource_template_definition(entry) for entry in self._resource_templates
                ],
            )

        async def _read_resource(ctx: ServerRequestContext, params: ReadResourceRequestParams):
            token = request_ctx.set(ctx)
            try:
                uri = str(params.uri)
                return _read_contents_to_result(uri, await self._read_resource(uri))
            finally:
                request_ctx.reset(token)

        async def _subscribe_resource(_ctx: ServerRequestContext, params: SubscribeRequestParams):
            if rejects_deprecated_method("resources/subscribe", self.protocol_era):
                message = deprecated_method_message("resources/subscribe")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            if str(params.uri) not in self._resources:
                raise ResourceNotFoundError(str(params.uri))
            return types.EmptyResult()

        async def _unsubscribe_resource(_ctx: ServerRequestContext, _params: UnsubscribeRequestParams):
            return types.EmptyResult()

        async def _list_prompts(_ctx: ServerRequestContext, _params: Optional[PaginatedRequestParams]):
            return types.ListPromptsResult(
                prompts=[self._build_prompt_definition(entry) for entry in self._prompts.values()],
                _meta=self._list_endpoint_cache_meta(),
            )

        async def _get_prompt(ctx: ServerRequestContext, params: GetPromptRequestParams):
            token = request_ctx.set(ctx)
            try:
                arguments = params.arguments or {}
                return await self._get_prompt(
                    params.name,
                    {str(key): str(value) for key, value in arguments.items()},
                )
            finally:
                request_ctx.reset(token)

        server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
        server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)
        server.add_request_handler("resources/list", PaginatedRequestParams, _list_resources)
        server.add_request_handler(
            "resources/templates/list", PaginatedRequestParams, _list_resource_templates
        )
        server.add_request_handler("resources/read", ReadResourceRequestParams, _read_resource)
        server.add_request_handler("resources/subscribe", SubscribeRequestParams, _subscribe_resource)
        server.add_request_handler("resources/unsubscribe", UnsubscribeRequestParams, _unsubscribe_resource)
        server.add_request_handler("prompts/list", PaginatedRequestParams, _list_prompts)
        server.add_request_handler("prompts/get", GetPromptRequestParams, _get_prompt)

        async def handle_list_tools(req=None):
            return ServerResult(await _list_tools(None, None))

        async def handle_call_tool(req=None):
            params = getattr(req, "params", None) if req is not None else None
            name = getattr(params, "name", "") if params is not None else ""
            arguments = (getattr(params, "arguments", None) or {}) if params is not None else {}
            task = getattr(params, "task", None) if params is not None else None
            return ServerResult(await self._call_tool(name, arguments, task=task))

        async def handle_list_resources(req=None):
            return ServerResult(await _list_resources(None, None))

        async def handle_list_resource_templates(req=None):
            return ServerResult(await _list_resource_templates(None, None))

        async def handle_read_resource(req):
            params = getattr(req, "params", req)
            uri = str(params.uri)
            return ServerResult(_read_contents_to_result(uri, await self._read_resource(uri)))

        async def handle_list_prompts(req=None):
            return ServerResult(await _list_prompts(None, None))

        async def handle_get_prompt(req):
            params = getattr(req, "params", req)
            arguments = getattr(params, "arguments", None) or {}
            return ServerResult(
                await self._get_prompt(
                    params.name,
                    {str(key): str(value) for key, value in arguments.items()},
                )
            )

        server.request_handlers[types.ListToolsRequest] = handle_list_tools
        server.request_handlers[types.CallToolRequest] = handle_call_tool
        server.request_handlers[types.ListResourcesRequest] = handle_list_resources
        server.request_handlers[types.ListResourceTemplatesRequest] = handle_list_resource_templates
        server.request_handlers[types.ReadResourceRequest] = handle_read_resource
        server.request_handlers[types.ListPromptsRequest] = handle_list_prompts
        server.request_handlers[types.GetPromptRequest] = handle_get_prompt

        self._register_task_handlers(server)
        self._register_initialized_handler(server)
        server.discover_handler = self.handle_server_discover
        server.initialize_handler = self.handle_sessionless_initialize

    def create_configured_mcp_server(self) -> NitroStackMcpServer:
        """
        Build a fresh, fully-wired low-level server instance sharing this
        application's tool/resource/prompt registries. A single shared server is
        sufficient for the Python `mcp` SDK's HTTP session managers (unlike the TS
        SDK's `Server`, `.run()` here does not bind a transport for the instance's
        lifetime), but this factory is kept available for isolated/stateless use
        cases (e.g. Phase 3 extensions) that want their own server instance.
        """
        server = NitroStackMcpServer(name=self.server_config.name, version=self.server_config.version)
        self._setup_handlers(server)
        return server

    # ------------------------------------------------------------------
    # Definition builders (registry entry -> wire-level `mcp.types` objects)
    # ------------------------------------------------------------------

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

        meta: Dict[str, Any] = {
            "is_initial": cfg.is_initial,
            "visibility": cfg.visibility,
            "task_support": cfg.task_support,
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
        if cfg.task_support and cfg.task_support != "forbidden":
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

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    def _pipeline_stages(self, method: Callable) -> Tuple[List[Type], List[Type], List[Type], List[Type], List[Type]]:
        return (
            getattr(method, "_mcp_guards", []),
            getattr(method, "_mcp_middleware", []),
            getattr(method, "_mcp_interceptors", []),
            getattr(method, "_mcp_pipes", []),
            getattr(method, "_mcp_filters", []),
        )

    def _to_call_tool_result(
        self,
        result: Any,
        component: Optional[Component] = None,
        context: Optional[ExecutionContext] = None,
    ) -> types.CallToolResult:
        if isinstance(result, InputRequiredResult):
            wire = result.to_wire_dict()
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=wire.get("message", "Input required"))],
                structuredContent=wire,
                isError=False,
            )
        mrtr_result = None
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            from nitrostack.protocol.mrtr import coerce_input_required_result

            mrtr_result = coerce_input_required_result(result)
        if mrtr_result is not None:
            wire = mrtr_result.to_wire_dict()
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=wire.get("message", "Input required"))],
                structuredContent=wire,
                isError=False,
            )
        if isinstance(result, types.CallToolResult):
            if component is not None:
                result = result.model_copy(
                    update={"meta": build_call_tool_result_meta(component, self._call_tool_result_meta(result))}
                )
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump()

        structured: Any = result
        result_meta: Optional[Dict[str, Any]] = None

        if component is not None:
            if component.transformer is not None:
                structured = component.transformer(result, context)
            if component.meta_transformer is not None:
                result_meta = component.meta_transformer(result, context) or {}
            if isinstance(structured, BaseModel):
                structured = structured.model_dump()

        if isinstance(result, dict) and "content" in result and "isError" in result:
            call_result = types.CallToolResult(**result)
            if component is not None:
                call_result = call_result.model_copy(
                    update={"meta": build_call_tool_result_meta(component, self._call_tool_result_meta(call_result))}
                )
            return call_result

        if isinstance(structured, dict):
            widget_meta = build_call_tool_result_meta(component, result_meta) if component else result_meta
            return types.CallToolResult(
                content=self._widget_result_content(structured, component),
                structuredContent=structured,
                **({"_meta": widget_meta} if widget_meta else {}),
                isError=False,
            )

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(structured))],
            isError=False,
        )

    @staticmethod
    def _call_tool_result_meta(result: types.CallToolResult) -> Optional[Dict[str, Any]]:
        """Read caller ``_meta`` from the field or pydantic extra (alias vs extra)."""
        if result.meta:
            return dict(result.meta)
        extra = getattr(result, "model_extra", None) or {}
        raw = extra.get("_meta") or extra.get("meta")
        return dict(raw) if isinstance(raw, dict) else None

    def _widget_result_content(self, structured: Dict[str, Any], component: Optional[Component]) -> List[Any]:
        """Text fallback plus data-filled HTML so Inspector/Studio can paint the live result."""
        content: List[Any] = [
            types.TextContent(type="text", text=json.dumps(structured, indent=2, default=str)),
        ]
        if component is None:
            return content
        mime = get_widget_mime_type()
        try:
            filled = component.html_with_data(structured)
        except Exception:
            logger.exception(
                "Widget HTML render failed for %s; returning JSON without embedded HTML",
                component.id,
            )
            filled = None
        if filled is not None:
            content.append(
                types.EmbeddedResource(
                    type="resource",
                    resource=types.TextResourceContents(
                        uri=component.resource_uri,
                        mimeType=mime,
                        text=filled,
                    ),
                )
            )
        content.append(
            types.ResourceLink(
                type="resource_link",
                uri=component.resource_uri,
                name=component.name,
                description=component.description,
                mimeType=mime,
            )
        )
        return content

    async def _call_tool(self, name: str, arguments: Dict[str, Any], task: Any = None):
        entry = self._tools.get(name)
        if entry is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Tool '{name}' not found")],
                isError=True,
            )

        cfg = entry.config
        tool_arguments, input_responses, request_state = split_mrtr_from_arguments(
            strip_tool_arguments(arguments)
        )
        rc = request_ctx.get(None)
        tool_arguments = _tool_arguments_with_mcp_params(
            tool_arguments,
            extract_mcp_param_headers(_http_headers_from_request_ctx(rc)),
            entry.input_model,
        )
        # Pydantic validates after accepting either Inspector top-level fields
        # or the older `{input: {...}}` wrap. Low-level jsonschema is off
        # (`validate_input=False`) so the wrap is not rejected against the
        # published top-level inputSchema.
        input_instance = parse_tool_input(entry.input_model, tool_arguments)
        guards, middleware, interceptors, pipes, filters = self._pipeline_stages(entry.method)

        # Task metadata: explicit ``params.task``, then the 1.x experimental
        # slot, then the official request context ``params`` mapping.
        task_metadata = task
        session = None
        progress_token = None
        if rc is not None:
            if task_metadata is None and getattr(rc, "experimental", None) is not None:
                task_metadata = rc.experimental.task_metadata
            params = getattr(rc, "params", None)
            if task_metadata is None:
                task_metadata = getattr(params, "task", None)
            if task_metadata is None and isinstance(params, Mapping):
                task_metadata = params.get("task")
            meta = getattr(rc, "meta", None)
            if isinstance(meta, Mapping):
                progress_token = meta.get("progress_token") or meta.get("progressToken")
            elif meta is not None:
                progress_token = getattr(meta, "progress_token", None) or getattr(
                    meta, "progressToken", None
                )
            session = getattr(rc, "session", None)
        auth_meta = _auth_metadata_from_request_ctx(rc)
        trace = _trace_context_from_request_ctx(rc)

        is_task = task_metadata is not None
        if cfg.task_support == "forbidden" and task_metadata is not None:
            raise MCPError(
                types.METHOD_NOT_FOUND,
                task_support_forbidden_message(cfg.name),
            )
        if cfg.task_support == "required" and task_metadata is None:
            raise MCPError(
                types.INVALID_REQUEST,
                task_support_required_message(cfg.name),
            )

        if is_task:
            ttl_ms = (
                task_metadata.ttl
                if task_metadata and task_metadata.ttl is not None
                else DEFAULT_TASK_TTL_MS
            )
            task_access = extract_task_access_context(rc)
            task = await self.task_manager.create_task(
                ttl_ms=ttl_ms,
                tool_name=cfg.name,
                owner_id=task_access.user_id if task_access else None,
                tenant_id=task_access.tenant_id if task_access else None,
                session_id=task_access.session_id if task_access else None,
            )
            task_id = task.id
            correlation_id, jsonrpc_id = _bind_correlation(rc)

            async def background_execution():
                task_ctx = ExecutionContext(
                    request_id=correlation_id,
                    correlation_id=correlation_id,
                    jsonrpc_id=jsonrpc_id,
                    tool_name=cfg.name,
                    metadata={"input": input_instance, **auth_meta},
                    input_responses=input_responses,
                    request_state=request_state,
                    trace=trace,
                )
                _apply_request_envelope(task_ctx, rc)
                task_ctx.task = TaskContext(
                    task_id,
                    self.task_manager,
                    session=session,
                    progress_token=progress_token,
                    correlation_id=correlation_id,
                )
                try:
                    result = await run_pipeline(
                        handler=entry.method,
                        handler_instance=entry.instance,
                        args=(input_instance, task_ctx),
                        kwargs={},
                        context=task_ctx,
                        guards=guards,
                        middleware=middleware,
                        interceptors=interceptors,
                        pipes=pipes,
                        filters=filters,
                        param_name="input",
                        param_type=entry.input_model,
                    )
                    if isinstance(result, InputRequiredResult):
                        await self.task_manager.require_input(
                            task_id,
                            result.to_wire_dict(),
                            progress=result.message or "Additional input required",
                        )
                    else:
                        await self.task_manager.complete_task(
                            task_id, self._to_call_tool_result(result, entry.component, task_ctx)
                        )
                except Exception as e:
                    try:
                        await self.task_manager.fail_task(task_id, e)
                    except (TaskAlreadyTerminalError, TaskExpiredError):
                        # Cancelled/expired while running — leave terminal state as-is.
                        pass

            asyncio.create_task(background_execution())
            return types.CreateTaskResult(task=self._task_data_to_mcp_task(task))

        correlation_id, jsonrpc_id = _bind_correlation(rc)
        ctx = ExecutionContext(
            request_id=correlation_id,
            correlation_id=correlation_id,
            jsonrpc_id=jsonrpc_id,
            tool_name=cfg.name,
            metadata={"input": input_instance, **auth_meta},
            input_responses=input_responses,
            request_state=request_state,
            trace=trace,
        )
        _apply_request_envelope(ctx, rc)
        ticket = self._in_flight.register(correlation_id, jsonrpc_id=jsonrpc_id)
        try:
            result = await run_pipeline(
                handler=entry.method,
                handler_instance=entry.instance,
                args=(input_instance, ctx),
                kwargs={},
                context=ctx,
                guards=guards,
                middleware=middleware,
                interceptors=interceptors,
                pipes=pipes,
                filters=filters,
                param_name="input",
                param_type=entry.input_model,
            )
        except Exception as exc:
            logger.exception("Tool %s failed", cfg.name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                isError=True,
            )
        finally:
            self._in_flight.discard(correlation_id)
        if ticket.cancel_requested.is_set():
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Request was cancelled.")],
                isError=True,
            )
        return self._to_call_tool_result(result, entry.component, ctx)

    async def _read_resource(self, uri: str) -> List[ReadResourceContents]:
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
            return [ReadResourceContents(content=result, mime_type=cfg.mime_type or "application/octet-stream")]

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
            messages.append(types.PromptMessage(role=role, content=types.TextContent(type="text", text=content)))

        return types.GetPromptResult(description=cfg.description, messages=messages)

    # ------------------------------------------------------------------
    # Task subsystem — registered on the low-level server's public
    # `request_handlers`/`notification_handlers` dicts.
    # ------------------------------------------------------------------

    def _task_data_to_mcp_task(self, task) -> types.Task:
        """Map TaskData to MCP Task. EXPIRED is not an MCP wire status — surface as error."""
        if task.status == TaskStatus.EXPIRED:
            raise MCPError(types.INVALID_PARAMS, f"Task {task.id} has expired")
        return types.Task(
            taskId=task.id,
            status=task.status.value,
            statusMessage=task.progress or "",
            createdAt=_iso_timestamp(task.created_at),
            lastUpdatedAt=_iso_timestamp(task.last_updated_at or task.created_at),
            ttl=task.ttl if task.ttl is not None else 0,
            pollInterval=task.poll_interval,
        )

    def _serialize_task_result_payload(self, result: Any) -> Any:
        if isinstance(result, types.CallToolResult):
            return result.model_dump(by_alias=True, exclude_none=True)
        if isinstance(result, BaseModel):
            return result.model_dump()
        return result

    def _build_get_task_result(self, task) -> types.GetTaskResult:
        mcp_task = self._task_data_to_mcp_task(task)
        payload: Dict[str, Any] = {
            "taskId": mcp_task.task_id,
            "status": mcp_task.status,
            "statusMessage": mcp_task.status_message,
            "createdAt": mcp_task.created_at,
            "lastUpdatedAt": mcp_task.last_updated_at,
            "ttl": mcp_task.ttl,
            "pollInterval": mcp_task.poll_interval,
        }
        if task.status == TaskStatus.COMPLETED and task.result is not None:
            payload["result"] = self._serialize_task_result_payload(task.result)
        elif task.status == TaskStatus.FAILED and task.error is not None:
            payload["error"] = {"message": str(task.error)}
        elif task.status == TaskStatus.INPUT_REQUIRED and task.result is not None:
            payload["result"] = task.result
        return GetTaskResult(**payload)

    def _register_task_handlers(self, server: NitroStackMcpServer) -> None:
        async def handle_list_tasks(req):
            if rejects_deprecated_method("tasks/list", self.protocol_era):
                message = deprecated_method_message("tasks/list")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            tasks_list = []
            access = extract_task_access_context(request_ctx.get(None))
            params = getattr(req, "params", req)
            cursor = getattr(params, "cursor", None) if params is not None else None
            tasks_page, next_cursor = await self.task_manager.list_tasks_page(
                access_context=access,
                cursor=cursor,
            )
            for t in tasks_page:
                if t.status == TaskStatus.EXPIRED:
                    continue
                tasks_list.append(self._task_data_to_mcp_task(t))
            return types.ListTasksResult(tasks=tasks_list, nextCursor=next_cursor)

        async def handle_get_task(req):
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None))
            try:
                t = await self.task_manager.get_task(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")
            return self._build_get_task_result(t)

        async def handle_cancel_task(req):
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None))
            try:
                await self.task_manager.cancel_task(task_id, access_context=access)
                t = await self.task_manager.get_task(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")
            except TaskExpiredError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} has expired")
            except TaskAlreadyTerminalError as e:
                raise MCPError(types.INVALID_PARAMS, str(e))
            mcp_task = self._task_data_to_mcp_task(t)
            return types.CancelTaskResult(
                taskId=mcp_task.task_id,
                status=mcp_task.status,
                statusMessage=mcp_task.status_message,
                createdAt=mcp_task.created_at,
                lastUpdatedAt=mcp_task.last_updated_at,
                ttl=mcp_task.ttl,
                pollInterval=mcp_task.poll_interval,
            )

        async def handle_get_task_payload(req):
            if rejects_deprecated_method("tasks/result", self.protocol_era):
                message = deprecated_method_message("tasks/result")
                raise MCPError(types.METHOD_NOT_FOUND, message or "Not supported")
            params = getattr(req, "params", req)
            task_id = getattr(params, "task_id", None) or getattr(params, "taskId", None)
            access = extract_task_access_context(request_ctx.get(None))
            try:
                t = await self.task_manager.wait_until_done(task_id, access_context=access)
            except TaskNotFoundError:
                raise MCPError(types.INVALID_PARAMS, f"Task {task_id} not found")
            if t.status == TaskStatus.COMPLETED:
                return t.result
            if t.status == TaskStatus.CANCELLED:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="Task was cancelled.")],
                    isError=True,
                )
            if t.status == TaskStatus.EXPIRED:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="Task has expired.")],
                    isError=True,
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(t.error or t.progress))],
                isError=True,
            )

        async def on_list_tasks(ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]):
            token = request_ctx.set(ctx)
            try:
                return await handle_list_tasks(types.ListTasksRequest(params=params))
            finally:
                request_ctx.reset(token)

        async def on_get_task(ctx: ServerRequestContext, params: types.GetTaskRequestParams):
            token = request_ctx.set(ctx)
            try:
                return await handle_get_task(types.GetTaskRequest(params=params))
            finally:
                request_ctx.reset(token)

        async def on_cancel_task(ctx: ServerRequestContext, params: types.CancelTaskRequestParams):
            token = request_ctx.set(ctx)
            try:
                return await handle_cancel_task(types.CancelTaskRequest(params=params))
            finally:
                request_ctx.reset(token)

        async def on_get_task_payload(ctx: ServerRequestContext, params: types.GetTaskPayloadRequestParams):
            token = request_ctx.set(ctx)
            try:
                return await handle_get_task_payload(types.GetTaskPayloadRequest(params=params))
            finally:
                request_ctx.reset(token)

        server.add_request_handler("tasks/list", PaginatedRequestParams, on_list_tasks)
        server.add_request_handler("tasks/get", types.GetTaskRequestParams, on_get_task)
        server.add_request_handler("tasks/cancel", types.CancelTaskRequestParams, on_cancel_task)
        server.add_request_handler("tasks/result", types.GetTaskPayloadRequestParams, on_get_task_payload)
        server.request_handlers[types.ListTasksRequest] = handle_list_tasks
        server.request_handlers[types.GetTaskRequest] = handle_get_task
        server.request_handlers[types.CancelTaskRequest] = handle_cancel_task
        server.request_handlers[types.GetTaskPayloadRequest] = handle_get_task_payload
        server.has_task_support = True

    def _register_initialized_handler(self, server: NitroStackMcpServer) -> None:
        async def handle_initialized(notification: types.InitializedNotification):
            for instance, method, config in self._initial_tools:
                try:
                    entry = self._tools[config.name]
                    try:
                        input_inst = entry.input_model()
                    except Exception:
                        input_inst = None

                    ctx = ExecutionContext(
                        request_id=f"initial-tool-{uuid.uuid4().hex[:8]}",
                        tool_name=config.name,
                        metadata={},
                    )
                    _apply_request_envelope(ctx, request_ctx.get(None))
                    guards, middleware, interceptors, pipes, filters = self._pipeline_stages(method)

                    await run_pipeline(
                        handler=method,
                        handler_instance=instance,
                        args=(input_inst, ctx),
                        kwargs={},
                        context=ctx,
                        guards=guards,
                        middleware=middleware,
                        interceptors=interceptors,
                        pipes=pipes,
                        filters=filters,
                        param_name="input",
                        param_type=entry.input_model,
                    )
                except Exception as e:
                    sys.stderr.write(f"Error executing initial tool '{config.name}': {e}\n")
                    sys.stderr.flush()

        async def on_initialized(ctx: ServerRequestContext, _params: Optional[types.NotificationParams]):
            token = request_ctx.set(ctx)
            try:
                await handle_initialized(types.InitializedNotification())
            finally:
                request_ctx.reset(token)

        server.add_notification_handler(
            "notifications/initialized", types.NotificationParams, on_initialized
        )
        server.notification_handlers[types.InitializedNotification] = handle_initialized

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    def _apply_protocol_era(self) -> ProtocolEra:
        """Re-read env/config, store the era, and log how it was chosen."""
        resolution = resolve_protocol_era_resolution(
            config_value=self.server_config.protocol_era
        )
        self.protocol_era = resolution.era
        self.protocol_era_source = resolution.source
        logger.info(resolution.log_line())
        return resolution.era

    def get_combined_app(
        self,
        *,
        max_sessions: Optional[int] = None,
        session_idle_timeout: Optional[float] = None,
        enable_cors: Optional[bool] = None,
        stateless: Optional[bool] = None,
        json_response: Optional[bool] = None,
    ) -> Any:
        """
        Build the Starlette app wiring the owned low-level server to the Streamable
        HTTP (`/mcp`), legacy SSE (`/sse` + `/mcp/messages/`), and health-check
        (`/mcp/health`) endpoints. See `nitrostack.transports.http.build_http_app`
        for the full behavior (session cap, CORS, DNS-rebinding protection).

        Any argument left as ``None`` falls back to env
        (``MCP_STATELESS``, ``NITRO_MCP_PROTOCOL_VERSION``, ``ENABLE_CORS``)
        then this app's ``ServerConfig.protocol_era``. Unset protocol era is ``auto``.
        """
        from nitrostack.transports.http import build_http_app

        era = self._apply_protocol_era()
        wire_mode = wire_mode_for_era(era)
        # Sessionful 1.x only when era is legacy. auto/modern stay sessionless.
        http_engine = resolve_http_engine(era, stateless=stateless)
        effective_stateless = http_engine == "sessionless"

        if enable_cors is None:
            env_cors = self._env_bool("ENABLE_CORS")
            enable_cors = True if env_cors is None else env_cors

        http_app = build_http_app(
            self,
            max_sessions=max_sessions if max_sessions is not None else self.server_config.max_sessions,
            session_idle_timeout=session_idle_timeout,
            enable_cors=enable_cors,
            stateless=effective_stateless,
            json_response=self.server_config.json_response if json_response is None else json_response,
            protocol_era=era,
            wire_mode=wire_mode,
            http_engine=http_engine,
        )

        if http_engine == "sessionless":
            from nitrostack.transports.middleware import wrap_stateless_transport

            def _discover_handler(_request):
                return self.handle_server_discover()

            def _initialize_handler(request):
                params = getattr(request, "params", None) or {}
                requested = params.get("protocolVersion") if isinstance(params, dict) else None
                return self.handle_sessionless_initialize(
                    requested if isinstance(requested, str) else None
                )

            http_app = wrap_stateless_transport(
                http_app,
                server_name=self.server_config.name,
                server_version=self.server_config.version,
                protocol_version=protocol_version_for_era(era, self.server_config.protocol_version),
                advertise_tasks=self._advertise_tasks_extension(),
                advertise_app=any(
                    getattr(entry, "component", None) is not None
                    for entry in getattr(self, "_tools", {}).values()
                ),
                custom_extensions=self._custom_extensions(),
                wire_mode=wire_mode,
                protocol_era=era,
                enable_cors=enable_cors,
                discover_handler=_discover_handler,
                initialize_handler=_initialize_handler,
            )

        return http_app

    async def _run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.mcp_server.run(read_stream, write_stream, self.mcp_server.create_initialization_options())

    @staticmethod
    def _env_int(name: str) -> Optional[int]:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    def _env_bool(name: str) -> Optional[bool]:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return None
        return raw.strip().lower() in ("1", "true", "yes", "on")

    async def start(self) -> None:
        """Starts the MCP application based on transport configurations."""
        # Start background OAuth discovery only when OAuthModule registered a service.
        # resolve(OAuthService) would otherwise auto-instantiate and fail closed.
        from nitrostack.auth.oauth import OAuthService, warn_if_oauth_fail_open

        container = DIContainer.get_instance()
        if container.has_value(OAuthService):
            oauth_service = container.resolve(OAuthService)
            oauth_service.start_discovery_server()
            warn_if_oauth_fail_open()

        transport = os.environ.get("MCP_TRANSPORT_TYPE") or self.server_config.transport_type
        node_env = os.environ.get("NODE_ENV", "development")
        port = resolve_http_port()
        host = resolve_http_host()

        json_response = self._env_bool("MCP_JSON_RESPONSE")
        if json_response is None:
            json_response = self.server_config.json_response
        max_sessions = self._env_int("MCP_MAX_SESSIONS") or self.server_config.max_sessions
        session_timeout_ms = self._env_int("MCP_SESSION_TIMEOUT_MS") or self.server_config.session_timeout_ms
        session_idle_timeout = (session_timeout_ms / 1000) if session_timeout_ms else None
        graceful_timeout_ms = self._env_int("MCP_GRACEFUL_SHUTDOWN_TIMEOUT_MS") or 10000

        if transport == "http":
            import uvicorn
            app = self.get_combined_app(
                max_sessions=max_sessions,
                session_idle_timeout=session_idle_timeout,
                json_response=json_response,
            )
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                timeout_graceful_shutdown=graceful_timeout_ms / 1000,
            )
            server = uvicorn.Server(config)
            await server.serve()
        elif transport == "dual" or (node_env == "production" and not transport):
            from nitrostack.transports.dual import run_dual

            app = self.get_combined_app(
                max_sessions=max_sessions,
                session_idle_timeout=session_idle_timeout,
                json_response=json_response,
            )
            await run_dual(
                self,
                app,
                host=host,
                port=port,
                graceful_timeout=graceful_timeout_ms / 1000,
            )
        else:
            # Default Stdio
            self._apply_protocol_era()
            from nitrostack.transports.stdio import safe_stdio_transport
            with safe_stdio_transport():
                await self._run_stdio()


class McpApplicationFactory:
    @classmethod
    async def create(cls, app_class: Type) -> McpApplication:
        """Bootstraps and instantiates the McpApplication class."""
        return McpApplication(app_class)
