"""Tests for MCP 2.0 architectural foundation (Doc 00)."""

import pytest

from nitrostack.core.app import ServerConfig
from nitrostack.protocol.constants import (
    LEGACY_SESSION_HEADER,
    MAX_CIMD_BYTES,
    MAX_SCHEMA_DEPTH,
)
from nitrostack.protocol.extensions import MCPExtensionId
from nitrostack.protocol.layers import RuntimeLayer
from nitrostack.protocol.version import (
    LEGACY_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from nitrostack.runtime.stateless import (
    DEFAULT_STATELESS_INVARIANTS,
    assert_stateless_headers,
)
from nitrostack.tasks.store import TaskStore
from nitrostack.tasks.types import TaskAccessContext, TERMINAL_TASK_STATUSES


class TestProtocolVersion:
    def test_modern_protocol_version(self):
        assert MODERN_PROTOCOL_VERSION == "2026-07-28"

    def test_legacy_protocol_version(self):
        assert LEGACY_PROTOCOL_VERSION == "2025-06-18"

    def test_supported_versions(self):
        assert MODERN_PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS


class TestDefenseInDepthConstants:
    def test_cimd_size_limit(self):
        assert MAX_CIMD_BYTES == 5120

    def test_schema_depth_limit(self):
        assert MAX_SCHEMA_DEPTH == 64

    def test_legacy_session_header_name(self):
        assert LEGACY_SESSION_HEADER == "Mcp-Session-Id"


class TestRuntimeLayers:
    def test_all_architecture_layers_defined(self):
        layers = {layer.value for layer in RuntimeLayer}
        assert layers == {
            "transport_security",
            "protocol_dispatcher",
            "registries",
            "task_management",
        }


class TestExtensions:
    def test_canonical_extension_ids(self):
        assert MCPExtensionId.APP.value == "io.modelcontextprotocol/app"
        assert MCPExtensionId.TASKS.value == "io.modelcontextprotocol/tasks"


class TestStatelessInvariants:
    def test_default_invariants(self):
        inv = DEFAULT_STATELESS_INVARIANTS
        assert inv.emit_session_headers is False
        assert inv.require_initialize_handshake is False
        assert inv.allow_session_stickiness is False

    def test_rejects_session_header_on_response(self):
        with pytest.raises(ValueError, match="Mcp-Session-Id"):
            assert_stateless_headers({"Mcp-Session-Id": "abc"})

    def test_allows_responses_without_session_header(self):
        assert_stateless_headers({"Content-Type": "application/json"})


class TestTaskStoreContract:
    def test_task_store_is_abstract(self):
        with pytest.raises(TypeError):
            TaskStore()  # type: ignore[abstract]

    def test_task_store_defines_required_methods(self):
        required = {"get", "set", "delete", "has", "list", "cleanup_expired"}
        assert required.issubset(set(TaskStore.__abstractmethods__))


class TestTaskAccessContext:
    def test_wire_alias_population(self):
        ctx = TaskAccessContext.model_validate(
            {"userId": "u1", "tenantId": "t1", "sessionId": "s1"}
        )
        assert ctx.user_id == "u1"
        assert ctx.tenant_id == "t1"
        assert ctx.session_id == "s1"


class TestTerminalTaskStatuses:
    def test_terminal_states(self):
        assert TERMINAL_TASK_STATUSES == frozenset({"completed", "failed", "cancelled"})


class TestServerConfigMcp20:
    def test_default_stateless_config(self):
        cfg = ServerConfig(name="test-server")
        assert cfg.protocol_version == MODERN_PROTOCOL_VERSION
        assert cfg.stateless is True
