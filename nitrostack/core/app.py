"""Compatibility exports for the public application API.

Implementation lives in focused app_* modules; those modules never import this facade.
The delegated startup method preserves the ``return http_app`` contract.
"""

from nitrostack.core.app_config import (
    DEFAULT_HTTP_PORT,
    GetTaskResult,
    ServerConfig,
    _apply_project_identity,
    _iso_timestamp,
    _project_identity,
    _read_dotenv_value,
    mcp_app,
    resolve_http_host,
    resolve_http_port,
)
from nitrostack.core.app_context import (
    _AUTH_META_KEYS,
    _apply_request_envelope,
    _auth_metadata_from_request_ctx,
    _bind_correlation,
    _http_headers_from_request_ctx,
    _read_contents_to_result,
    _request_meta_from_ctx,
    _tool_arguments_with_mcp_params,
    _trace_context_from_request_ctx,
)
from nitrostack.core.app_entries import _PromptEntry, _ResourceEntry, _ToolEntry
from nitrostack.core.app_runtime import McpApplication, McpApplicationFactory
from nitrostack.core.app_schema import (
    _is_blank_inspector_value,
    _is_null_schema,
    _omit_blank_optional_fields,
    _unwrap_nullable_schema,
    get_pydantic_model,
    inspector_friendly_schema,
    parse_tool_input,
    tool_json_schema,
)

__all__ = [
    "DEFAULT_HTTP_PORT",
    "GetTaskResult",
    "McpApplication",
    "McpApplicationFactory",
    "ServerConfig",
    "_AUTH_META_KEYS",
    "_PromptEntry",
    "_ResourceEntry",
    "_ToolEntry",
    "_apply_project_identity",
    "_apply_request_envelope",
    "_auth_metadata_from_request_ctx",
    "_bind_correlation",
    "_http_headers_from_request_ctx",
    "_is_blank_inspector_value",
    "_is_null_schema",
    "_iso_timestamp",
    "_omit_blank_optional_fields",
    "_project_identity",
    "_read_contents_to_result",
    "_read_dotenv_value",
    "_request_meta_from_ctx",
    "_tool_arguments_with_mcp_params",
    "_trace_context_from_request_ctx",
    "_unwrap_nullable_schema",
    "get_pydantic_model",
    "inspector_friendly_schema",
    "mcp_app",
    "parse_tool_input",
    "resolve_http_host",
    "resolve_http_port",
    "tool_json_schema",
]
