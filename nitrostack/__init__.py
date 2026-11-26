# NitroStack Python SDK Public API Exports

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from nitrostack._export_map import EXPORT_LOCATIONS

_PKG_ROOT = Path(__file__).resolve().parent


def _subpackage_names() -> frozenset[str]:
    names = {m.name for m in pkgutil.iter_modules([str(_PKG_ROOT)]) if not m.name.startswith("_")}
    for path in _PKG_ROOT.iterdir():
        if path.is_dir() and not path.name.startswith("_") and path.name != "__pycache__":
            names.add(path.name)
    return frozenset(names)

# NitroStack Python SDK Public API Exports

try:
    from importlib.metadata import PackageNotFoundError, version as _package_version

    __version__ = _package_version("nitrostack")
except PackageNotFoundError:
    # Keep the SDK version available when running directly from a source checkout.
    try:
        from pathlib import Path

        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib

        _project_metadata = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        )
        __version__ = str(_project_metadata["project"]["version"])
    except (ImportError, KeyError, OSError, ValueError, TypeError):
        __version__ = "unknown"


__all__ = ['AuthorizationFlow',
 'ClientRegistration',
 'OAuth2Client',
 'OAuth2ClientConfig',
 'OAuthClientError',
 'FileTokenStore',
 'MemoryTokenStore',
 'StoredToken',
 'TokenStore',
 'TokenStoreError',
 'calculate_expiration',
 'create_default_token_store',
 'get_default_store_path',
 'get_well_known_metadata_uris',
 'is_token_expired',
 'parse_www_authenticate_header',
 'token_response_to_stored',
 '__version__',
 'DataSpilloverInterceptor',
 'FileSpilloverStore',
 'MemorySpilloverStore',
 'PayloadTooLargeError',
 'SpilloverEntry',
 'SpilloverStore',
 'generate_preview',
 'tool',
 'resource',
 'prompt',
 'initial_tool',
 'widget',
 'ToolAnnotations',
 'ResourceAnnotations',
 'PromptArgument',
 'PromptMessage',
 'ToolInvocation',
 'ToolExamples',
 'ExecutionContext',
 'AuthContext',
 'injectable',
 'DIContainer',
 'module',
 'mcp_app',
 'McpApplicationFactory',
 'ServerConfig',
 'ToolExecutionError',
 'ValidationError',
 'RateLimitExceededError',
 'ResourceNotFoundError',
 'PromptNotFoundError',
 'use_guards',
 'use_middleware',
 'use_interceptors',
 'use_pipes',
 'use_filters',
 'ApiKeyGuard',
 'JwtGuard',
 'OAuthGuard',
 'cache',
 'rate_limit',
 'health_check',
 'TransformTelemetry',
 'ServerHealthPayload',
 'on_event',
 'EventEmitter',
 'ApiKeyModule',
 'JWTModule',
 'OAuthModule',
 'OAuthService',
 'generate_www_authenticate_header',
 'validate_client_identifier_url',
 'resolve_cimd',
 'is_blocked_ip',
 'CimdValidationError',
 'CimdFetchError',
 'validate_authorization_iss',
 'AuthorizationIssuerMismatchError',
 'generate_code_challenge',
 'generate_code_verifier',
 'generate_pkce_params',
 'is_valid_code_verifier',
 'validate_pkce_support',
 'verify_pkce',
 'has_all_scopes',
 'has_any_scope',
 'has_scope',
 'require_scopes',
 'ConfigurationError',
 'DependencyResolutionError',
 'OAuthError',
 'TokenInactiveError',
 'AudienceMismatchError',
 'ConfigModule',
 'ConfigService',
 'SecretValue',
 'is_secret_value',
 'unwrap_secret',
 'setup_jwt_auth',
 'setup_api_key_auth',
 'setup_oauth_auth',
 'generate_test_credentials',
 'validate_auth_env',
 'print_auth_setup_instructions',
 'NitroTestingModule',
 'TaskContext',
 'LogRecord',
 'LogEmitter',
 'LogEmitterHandler',
 'log_emitter',
 'TaskCancelledError',
 'TaskManager',
 'TaskData',
 'TaskStatus',
 'TERMINAL_STATUSES',
 'is_terminal_status',
 'TaskNotFoundError',
 'TaskAlreadyTerminalError',
 'InvalidTaskTransitionError',
 'TaskExpiredError',
 'WidgetExample',
 'WidgetManifest',
 'WidgetMetadata',
 'WidgetRegistry',
 'get_widget_registry',
 'widget_resource_uri',
 'Component',
 'WidgetCsp',
 'WidgetOptions',
 'create_component',
 'get_app_mode',
 'get_widget_mime_type',
 'is_mcp_app_mode',
 'is_openai_mode',
 'OPENAI_SKYBRIDGE_MIME_TYPE',
 'RESOURCE_MIME_TYPE_MCP_APP',
 'RESOURCE_MIME_TYPE_OPENAI',
 'NitroTestingModule',
 'MODERN_PROTOCOL_VERSION',
 'JsonRpcErrorCode',
 'InputRequest',
 'InputRequiredResult',
 'accepted_content',
 'input_required',
 'TaskAccessContext',
 'TaskStore',
 'InMemoryTaskStore',
 'check_task_access',
 'extract_task_access_context',
 'StatelessInvariants',
 'assert_stateless_headers']

def __getattr__(name: str) -> object:
    location = EXPORT_LOCATIONS.get(name)
    if location is not None:
        module_name, attr = location
        module = importlib.import_module(module_name)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    if name.startswith("_") or name not in _subpackage_names():
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{name}":
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        raise
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *__all__, *_subpackage_names()})

if TYPE_CHECKING:
    from nitrostack.widgets.registry import WidgetExample, WidgetManifest, WidgetMetadata, WidgetRegistry, get_widget_registry  # noqa: F401
    from nitrostack.core.interceptors import DataSpilloverInterceptor, FileSpilloverStore, MemorySpilloverStore, PayloadTooLargeError, SpilloverEntry, SpilloverStore, generate_preview  # noqa: F401
    from nitrostack.core.decorators import tool, resource, prompt, initial_tool, widget, ToolAnnotations, ResourceAnnotations, PromptArgument, PromptMessage, ToolInvocation, ToolExamples, widget_resource_uri  # noqa: F401
    from nitrostack.core.context import ExecutionContext, AuthContext, TaskContext  # noqa: F401
    from nitrostack.core.log_emitter import LogRecord, LogEmitter, LogEmitterHandler, log_emitter  # noqa: F401
    from nitrostack.core.task import TaskManager, TaskData, TaskStatus, TERMINAL_STATUSES, is_terminal_status  # noqa: F401
    from nitrostack.core.di import injectable, DIContainer  # noqa: F401
    from nitrostack.core.module import module  # noqa: F401
    from nitrostack.core.app import mcp_app, McpApplicationFactory, ServerConfig  # noqa: F401
    from nitrostack.core.errors import ToolExecutionError, ValidationError, RateLimitExceededError, ResourceNotFoundError, PromptNotFoundError, TaskCancelledError, TaskNotFoundError, TaskAlreadyTerminalError, InvalidTaskTransitionError, TaskExpiredError, ConfigurationError, DependencyResolutionError, OAuthError, TokenInactiveError, AudienceMismatchError  # noqa: F401
    from nitrostack.core.pipeline import use_guards, use_middleware, use_interceptors, use_pipes, use_filters, ApiKeyGuard, JwtGuard, OAuthGuard  # noqa: F401
    from nitrostack.core.additional_decorators import cache, rate_limit, health_check, TransformTelemetry, ServerHealthPayload  # noqa: F401
    from nitrostack.events.event_emitter import on_event, EventEmitter  # noqa: F401
    from nitrostack.auth.api_key import ApiKeyModule  # noqa: F401
    from nitrostack.auth.jwt import JWTModule  # noqa: F401
    from nitrostack.auth.oauth import OAuthModule, OAuthService, generate_www_authenticate_header  # noqa: F401
    from nitrostack.auth.cimd import CimdFetchError, CimdValidationError, is_blocked_ip, resolve_cimd, validate_client_identifier_url  # noqa: F401
    from nitrostack.auth.oauth_security import AuthorizationIssuerMismatchError, validate_authorization_iss  # noqa: F401
    from nitrostack.auth.pkce import generate_code_challenge, generate_code_verifier, generate_pkce_params, is_valid_code_verifier, validate_pkce_support, verify_pkce  # noqa: F401
    from nitrostack.auth.client import AuthorizationFlow, ClientRegistration, OAuth2Client, OAuth2ClientConfig, OAuthClientError, get_well_known_metadata_uris, parse_www_authenticate_header  # noqa: F401
    from nitrostack.auth.token_store import FileTokenStore, MemoryTokenStore, StoredToken, TokenStore, TokenStoreError, calculate_expiration, create_default_token_store, get_default_store_path, is_token_expired, token_response_to_stored  # noqa: F401
    from nitrostack.auth.scopes import has_all_scopes, has_any_scope, has_scope, require_scopes  # noqa: F401
    from nitrostack.auth.config import ConfigModule, ConfigService  # noqa: F401
    from nitrostack.auth.secret import SecretValue, is_secret_value, unwrap_secret  # noqa: F401
    from nitrostack.auth.setup import generate_test_credentials, print_auth_setup_instructions, setup_api_key_auth, setup_jwt_auth, setup_oauth_auth, validate_auth_env  # noqa: F401
    from nitrostack.widgets import Component, WidgetCsp, WidgetOptions, create_component, get_app_mode, get_widget_mime_type, is_mcp_app_mode, is_openai_mode, OPENAI_SKYBRIDGE_MIME_TYPE, RESOURCE_MIME_TYPE_MCP_APP, RESOURCE_MIME_TYPE_OPENAI  # noqa: F401
    from nitrostack.testing import NitroTestingModule  # noqa: F401
    from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION  # noqa: F401
    from nitrostack.protocol.errors import JsonRpcErrorCode  # noqa: F401
    from nitrostack.protocol.mrtr import InputRequest, InputRequiredResult, accepted_content, input_required  # noqa: F401
    from nitrostack.tasks import InMemoryTaskStore, TaskAccessContext, TaskStore  # noqa: F401
    from nitrostack.tasks.authorization import check_task_access, extract_task_access_context  # noqa: F401
    from nitrostack.runtime import StatelessInvariants, assert_stateless_headers  # noqa: F401
