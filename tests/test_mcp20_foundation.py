"""Tests for MCP 2.0 architectural foundation."""

import pytest

from nitrostack.core.app import ServerConfig, resolve_http_host
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
    http_engine_for_era,
    needs_modern_engine,
    needs_sessionful_engine,
    protocol_version_for_era,
    resolve_protocol_era,
    stateless_for_era,
    wire_mode_for_era,
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
        assert cfg.stateless is False


class TestTypescriptCompatibleProtocolEra:
    @pytest.mark.parametrize(
        "raw,era",
        [
            ("auto", "auto"),
            ("both", "auto"),
            ("dual", "auto"),
            ("dual-spec", "auto"),
            ("AUTO", "auto"),
            ("2026-07-28", "modern"),
            ("2026", "modern"),
            ("modern", "modern"),
            ("latest", "modern"),
            ("Modern", "modern"),
            ("legacy", "legacy"),
            ("2025-06-18", "legacy"),
            ("2025-11-25", "legacy"),
            ("2025", "legacy"),
            ("", "auto"),
            (None, "auto"),
            ("unknown-era", "auto"),
            ("mcp-2026", "auto"),
            ("2026-06-18", "auto"),
        ],
    )
    def test_resolve_protocol_era(self, raw, era, monkeypatch):
        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        assert resolve_protocol_era(raw) == era

    def test_reads_nitro_mcp_protocol_version_env(self, monkeypatch):
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "2026-07-28")
        assert resolve_protocol_era() == "modern"
        assert stateless_for_era(resolve_protocol_era()) is True
        assert protocol_version_for_era("modern") == MODERN_PROTOCOL_VERSION

    def test_unknown_env_alias_is_auto_not_modern(self, monkeypatch):
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "unknown-era")
        assert resolve_protocol_era() == "auto"

    def test_legacy_era_is_sessionful(self):
        assert stateless_for_era("legacy") is False
        assert protocol_version_for_era("legacy") == LEGACY_PROTOCOL_VERSION

    def test_unset_era_defaults_to_auto_without_forcing_stateless(self, monkeypatch):
        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        assert resolve_protocol_era() == "auto"
        assert stateless_for_era("auto") is None
        assert protocol_version_for_era("auto") == MODERN_PROTOCOL_VERSION

    def test_mcp_stateless_true_forces_modern(self, monkeypatch):
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "legacy")
        assert resolve_protocol_era(stateless_override="true") == "modern"

    def test_mcp_stateless_false_forces_legacy(self, monkeypatch):
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "modern")
        monkeypatch.setenv("MCP_STATELESS", "false")
        assert resolve_protocol_era() == "legacy"
        assert stateless_for_era("legacy") is False

    def test_auto_is_not_modern_and_does_not_force_stateless(self):
        assert resolve_protocol_era("auto") != resolve_protocol_era("modern")
        assert stateless_for_era("auto") is None
        assert stateless_for_era("modern") is True
        assert wire_mode_for_era("auto") == "stateless"
        assert wire_mode_for_era("modern") == "reject"
        assert wire_mode_for_era("legacy") == "sessionful"
        assert needs_modern_engine("auto") is True
        assert needs_modern_engine("modern") is True
        assert needs_modern_engine("legacy") is False
        assert needs_sessionful_engine("auto") is False
        assert needs_sessionful_engine("legacy") is True
        assert http_engine_for_era("auto") == "sessionless"
        assert http_engine_for_era("modern") == "sessionless"
        assert http_engine_for_era("legacy") == "sessionful"

    def test_config_era_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        assert resolve_protocol_era(config_value="legacy") == "legacy"
        assert resolve_protocol_era(config_value="modern") == "modern"
        assert resolve_protocol_era(config_value="2026-07-28") == "modern"

    def test_env_wins_over_config_era(self, monkeypatch):
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        monkeypatch.setenv("NITRO_MCP_PROTOCOL_VERSION", "auto")
        assert resolve_protocol_era(config_value="legacy") == "auto"

    def test_stateless_override_wins_over_config_era(self, monkeypatch):
        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.setenv("MCP_STATELESS", "true")
        assert resolve_protocol_era(config_value="legacy") == "modern"

    def test_invalid_config_era_matches_invalid_env(self, monkeypatch):
        monkeypatch.delenv("NITRO_MCP_PROTOCOL_VERSION", raising=False)
        monkeypatch.delenv("MCP_STATELESS", raising=False)
        assert resolve_protocol_era("unknown-era") == "auto"
        assert resolve_protocol_era(config_value="unknown-era") == "auto"

    def test_server_config_protocol_era_default_is_unset(self):
        cfg = ServerConfig(name="test-server")
        assert cfg.protocol_era is None
        assert resolve_protocol_era(config_value=cfg.protocol_era) == "auto"


class TestTypescriptCompatibleHost:
    def test_host_defaults_to_all_interfaces(self, monkeypatch):
        monkeypatch.delenv("HOST", raising=False)
        assert resolve_http_host() == "0.0.0.0"

    def test_host_env_matches_typescript(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        assert resolve_http_host() == "localhost"
