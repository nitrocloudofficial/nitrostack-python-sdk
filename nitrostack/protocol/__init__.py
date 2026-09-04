"""MCP 2.0 protocol layer — version constants, extensions, and wire invariants."""

from nitrostack.protocol.constants import (
    LEGACY_SESSION_HEADER,
    MAX_CIMD_BYTES,
    MAX_SCHEMA_DEPTH,
)
from nitrostack.protocol.contracts import (
    build_cache_hint_meta,
    build_prompt_get_result,
    build_resource_blob_content,
    build_resource_text_content,
)
from nitrostack.protocol.deprecated import deprecated_method_message
from nitrostack.protocol.discovery import build_discover_result
from nitrostack.protocol.errors import ERROR_CODE_MESSAGES, JsonRpcErrorCode
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.jsonrpc import (
    JsonRpcWireError,
    build_ping_response,
    build_tool_error_result,
    jsonrpc_error,
    jsonrpc_success,
    map_exception_to_jsonrpc,
    parse_jsonrpc_request,
)
from nitrostack.protocol.layers import RuntimeLayer
from nitrostack.protocol.cache_hints import (
    build_list_endpoint_cache_hint_meta,
    resolve_resource_cache_hint_meta,
    resolve_tool_cache_hint_meta,
)
from nitrostack.protocol.observability import TraceContext, extract_trace_context, trace_context_from_request_meta
from nitrostack.protocol.meta import RequestMeta, extract_request_meta, split_params_and_meta
from nitrostack.protocol.mrtr import (
    InputRequest,
    InputRequiredResult,
    accepted_content,
    build_input_required_jsonrpc_result,
    input_required,
    split_mrtr_tool_params,
)
from nitrostack.protocol.resources import resolve_resource_uri, uri_template_to_pattern
from nitrostack.protocol.schema import (
    JSON_SCHEMA_2020_12_URI,
    bound_schema_depth,
    normalize_input_schema,
    normalize_output_schema,
)
from nitrostack.protocol.tasks import (
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_TASK_TTL_MS,
    RESULT_TYPE_TASK,
    build_task_create_jsonrpc_result,
    task_support_forbidden_message,
    task_support_required_message,
    ttl_ms_to_seconds,
    ttl_seconds_to_ms,
)
from nitrostack.protocol.version import (
    LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)

__all__ = [
    "LEGACY_PROTOCOL_VERSION",
    "MODERN_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "MCPExtensionId",
    "RuntimeLayer",
    "LEGACY_SESSION_HEADER",
    "MAX_CIMD_BYTES",
    "MAX_SCHEMA_DEPTH",
    "JsonRpcErrorCode",
    "ERROR_CODE_MESSAGES",
    "RequestMeta",
    "extract_request_meta",
    "split_params_and_meta",
    "deprecated_method_message",
    "build_discover_result",
    "parse_jsonrpc_request",
    "build_ping_response",
    "jsonrpc_success",
    "jsonrpc_error",
    "build_tool_error_result",
    "map_exception_to_jsonrpc",
    "JsonRpcWireError",
    "JSON_SCHEMA_2020_12_URI",
    "bound_schema_depth",
    "normalize_input_schema",
    "normalize_output_schema",
    "resolve_resource_uri",
    "uri_template_to_pattern",
    "build_cache_hint_meta",
    "build_resource_text_content",
    "build_resource_blob_content",
    "build_prompt_get_result",
    "InputRequest",
    "InputRequiredResult",
    "accepted_content",
    "input_required",
    "split_mrtr_tool_params",
    "build_input_required_jsonrpc_result",
    "RESULT_TYPE_TASK",
    "DEFAULT_TASK_TTL_MS",
    "DEFAULT_POLL_INTERVAL_MS",
    "build_task_create_jsonrpc_result",
    "ttl_ms_to_seconds",
    "ttl_seconds_to_ms",
    "task_support_forbidden_message",
    "task_support_required_message",
    "TraceContext",
    "extract_trace_context",
    "trace_context_from_request_meta",
    "build_list_endpoint_cache_hint_meta",
    "resolve_tool_cache_hint_meta",
    "resolve_resource_cache_hint_meta",
]
