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
    accepts_sessionless_initialize,
    http_engine_for_era,
    rejects_legacy_initialize,
    needs_modern_engine,
    needs_sessionful_engine,
    protocol_era_for_wire_mode,
    protocol_version_for_era,
    resolve_protocol_era,
    stateless_for_era,
    supported_protocol_versions_for_era,
    wire_mode_for_era,
)
from nitrostack.runtime.stateless import (
    DEFAULT_STATELESS_INVARIANTS,
    assert_stateless_headers,
    has_incoming_session_id,
    is_unsupported_protocol_version,
    request_protocol_version,
    sessionless_rejects_incoming_session_id,
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

    def test_supported_versions_per_era(self):
        assert supported_protocol_versions_for_era("modern") == frozenset(
            {MODERN_PROTOCOL_VERSION}
        )
        assert supported_protocol_versions_for_era("legacy") == frozenset(
            {LEGACY_PROTOCOL_VERSION}
        )
        assert supported_protocol_versions_for_era("auto") == frozenset(
            {MODERN_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION}
        )
        assert protocol_era_for_wire_mode("reject") == "modern"
        assert protocol_era_for_wire_mode("stateless") == "auto"
        assert protocol_era_for_wire_mode("sessionful") == "legacy"

    def test_unsupported_protocol_version_contract(self):
        assert request_protocol_version("2026-07-28", "2025-06-18") == "2026-07-28"
        assert request_protocol_version(None, "2025-06-18") == "2025-06-18"
        assert request_protocol_version(None, None) is None
        assert is_unsupported_protocol_version("1999-01-01", "auto") is True
        assert is_unsupported_protocol_version("2026-07-28", "auto") is False
        assert is_unsupported_protocol_version("2025-06-18", "auto") is False
        assert is_unsupported_protocol_version("2025-06-18", "modern") is True
        assert is_unsupported_protocol_version(None, "modern") is False


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

    def test_detects_incoming_session_id(self):
        assert has_incoming_session_id({"Mcp-Session-Id": "abc"}) is True
        assert has_incoming_session_id({"mcp-session-id": "abc"}) is True
        assert has_incoming_session_id({"Content-Type": "application/json"}) is False
        assert has_incoming_session_id({"Mcp-Session-Id": "  "}) is False

    def test_sessionless_engines_reject_incoming_session_id(self):
        assert sessionless_rejects_incoming_session_id("reject") is True
        assert sessionless_rejects_incoming_session_id("stateless") is True
        assert sessionless_rejects_incoming_session_id("sessionful") is False


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
        assert accepts_sessionless_initialize("auto") is True
        assert accepts_sessionless_initialize("modern") is False
        assert accepts_sessionless_initialize("legacy") is False
        assert rejects_legacy_initialize("modern") is True
        assert rejects_legacy_initialize("auto") is False
        assert rejects_legacy_initialize("legacy") is False

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
    def test_host_defaults_to_loopback(self, monkeypatch):
        monkeypatch.delenv("HOST", raising=False)
        assert resolve_http_host() == "127.0.0.1"

    def test_empty_host_defaults_to_loopback(self, monkeypatch):
        monkeypatch.setenv("HOST", "  ")
        assert resolve_http_host() == "127.0.0.1"

    def test_host_all_interfaces_still_available(self, monkeypatch):
        monkeypatch.setenv("HOST", "0.0.0.0")
        assert resolve_http_host() == "0.0.0.0"

    def test_host_env_matches_typescript(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        assert resolve_http_host() == "localhost"


class TestTrustedReverseProxy:
    def test_default_ignores_forwarded_host(self, monkeypatch):
        from nitrostack.transports.proxy import public_origin, request_host_for_cimd

        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
        headers = {
            "Host": "internal:3000",
            "X-Forwarded-Host": "mcp.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "10.0.0.5",
        }
        assert public_origin(headers, peer="8.8.8.8") == "http://internal:3000"
        assert request_host_for_cimd(headers, peer="8.8.8.8") == "internal"

    def test_trusted_proxy_honors_forwarded_host(self, monkeypatch):
        from nitrostack.transports.proxy import public_url, request_host_for_cimd

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        headers = {
            "Host": "internal:3000",
            "X-Forwarded-Host": "mcp.example.com",
            "X-Forwarded-Proto": "https",
        }
        assert public_url(headers, peer="10.0.0.5", path="/mcp") == "https://mcp.example.com/mcp"
        assert request_host_for_cimd(headers, peer="10.0.0.5") == "mcp.example.com"

    def test_cidr_allow_list(self, monkeypatch):
        from nitrostack.transports.proxy import peer_is_trusted

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
        assert peer_is_trusted("10.1.2.3") is True
        assert peer_is_trusted("11.0.0.1") is False

    def test_x_forwarded_for_does_not_grant_trust(self, monkeypatch):
        from nitrostack.transports.proxy import public_origin

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        headers = {
            "Host": "internal:3000",
            "X-Forwarded-For": "10.0.0.5",
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        }
        assert public_origin(headers, peer="8.8.8.8") == "http://internal:3000"

    def test_untrusted_forwarded_host_cannot_rebind_cimd(self, monkeypatch):
        from nitrostack.auth.cimd import cimd_host_matches_request, request_host_for_cimd

        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        headers = {
            "Host": "mcp.nitrostack.io",
            "X-Forwarded-Host": "evil.example",
        }
        url = "https://evil.example/oauth/client-metadata.json"
        assert request_host_for_cimd(headers, peer="8.8.8.8") == "mcp.nitrostack.io"
        assert cimd_host_matches_request(url, headers, peer="8.8.8.8") is False

    def test_trusted_proxy_cimd_host_matches_forwarded_host(self, monkeypatch):
        from nitrostack.auth.cimd import cimd_host_matches_request, request_host_for_cimd

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        headers = {
            "Host": "internal:3000",
            "X-Forwarded-Host": "app.nitrostack.io",
        }
        url = "https://app.nitrostack.io/oauth/client-metadata.json"
        assert request_host_for_cimd(headers, peer="10.0.0.5") == "app.nitrostack.io"
        assert cimd_host_matches_request(url, headers, peer="10.0.0.5") is True
