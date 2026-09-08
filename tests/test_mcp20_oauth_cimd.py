"""Tests for MCP 2.0 OAuth 2.1, CIMD, and SSRF security."""

import asyncio
import json
import socket
from unittest.mock import patch

import pytest

from nitrostack.auth.cimd import (
    CimdFetchError,
    CimdValidationError,
    _fetch_cimd_bytes,
    assert_safe_fetch_target,
    cimd_host_matches_request,
    cimd_peer_is_acceptable,
    is_blocked_ip,
    request_host_for_cimd,
    resolve_cimd,
    validate_client_identifier_url,
)
from nitrostack.auth.oauth_module import apply_cimd_to_registration_body, build_protected_resource_metadata
from nitrostack.auth.oauth_security import (
    AuthorizationIssuerMismatchError,
    validate_authorization_iss,
)
from nitrostack.protocol.constants import MAX_CIMD_BYTES


class _StubOAuthService:
    resource_uri = "https://mcp.nitrostack.io/mcp"
    authorization_servers = ["https://auth.nitrostack.io"]
    scopes_supported = ["mcp:tools", "mcp:resources", "mcp:prompts"]


class TestClientIdentifierUrlValidation:
    def test_accepts_https_with_path(self):
        url = "https://app.nitrostack.io/oauth/client-metadata.json"
        assert validate_client_identifier_url(url) == url

    def test_rejects_bare_domain(self):
        with pytest.raises(CimdValidationError, match="non-root path"):
            validate_client_identifier_url("https://example.com/")

    def test_rejects_userinfo(self):
        with pytest.raises(CimdValidationError, match="userinfo"):
            validate_client_identifier_url("https://user:pass@example.com/oauth/client.json")

    def test_rejects_fragment(self):
        with pytest.raises(CimdValidationError, match="fragment"):
            validate_client_identifier_url("https://example.com/oauth/client.json#x")

    def test_rejects_path_traversal(self):
        with pytest.raises(CimdValidationError, match="\\.\\."):
            validate_client_identifier_url("https://example.com/oauth/../client.json")

    def test_allows_loopback_http_when_enabled(self):
        url = "http://127.0.0.1/oauth/client-metadata.json"
        assert validate_client_identifier_url(url, allow_loopback=True) == url


class TestBlockedIpRanges:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "192.168.1.10",
            "::1",
            "fc00::1",
        ],
    )
    def test_blocks_special_use_addresses(self, ip):
        assert is_blocked_ip(ip) is True

    def test_allows_public_ipv4(self):
        assert is_blocked_ip("8.8.8.8") is False


class TestCimdResolver:
    def test_blocks_private_dns_resolution(self):
        async def _run():
            url = "https://metadata.example.com/oauth/client.json"
            with patch(
                "socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
            ):
                with pytest.raises(CimdFetchError, match="blocked IP"):
                    await assert_safe_fetch_target(url)

        asyncio.run(_run())

    def test_rejects_redirects(self):
        async def _run():
            url = "https://app.nitrostack.io/oauth/client-metadata.json"

            def fake_fetch(_url, *, timeout_sec, pinned_ip=None):
                raise CimdFetchError("HTTP redirects are not allowed for CIMD fetch")

            with patch("nitrostack.auth.cimd.assert_safe_fetch_target", return_value=None):
                with patch("nitrostack.auth.cimd._fetch_cimd_bytes", side_effect=fake_fetch):
                    with pytest.raises(CimdFetchError, match="redirect"):
                        await resolve_cimd(url)

        asyncio.run(_run())

    def test_rejects_oversized_payload(self):
        async def _run():
            url = "https://app.nitrostack.io/oauth/client-metadata.json"
            oversized = b"x" * (MAX_CIMD_BYTES + 1)

            with patch("nitrostack.auth.cimd.assert_safe_fetch_target", return_value=None):
                with patch("nitrostack.auth.cimd._fetch_cimd_bytes", return_value=oversized):
                    with pytest.raises(CimdFetchError, match="maximum size"):
                        await resolve_cimd(url)

        asyncio.run(_run())

    def test_rejects_client_id_mismatch(self):
        async def _run():
            url = "https://app.nitrostack.io/oauth/client-metadata.json"
            body = json.dumps(
                {
                    "client_id": "https://evil.example/oauth/client-metadata.json",
                    "redirect_uris": ["https://app.nitrostack.io/cb"],
                }
            ).encode()

            with patch("nitrostack.auth.cimd.assert_safe_fetch_target", return_value=None):
                with patch("nitrostack.auth.cimd._fetch_cimd_bytes", return_value=body):
                    with pytest.raises(CimdValidationError, match="does not match"):
                        await resolve_cimd(url)

        asyncio.run(_run())

    def test_returns_document_when_valid(self):
        async def _run():
            url = "https://app.nitrostack.io/oauth/client-metadata.json"
            body = json.dumps(
                {
                    "client_id": url,
                    "client_name": "NitroStudio",
                    "redirect_uris": ["https://app.nitrostack.io/auth/callback"],
                }
            ).encode()

            with patch("nitrostack.auth.cimd.assert_safe_fetch_target", return_value=None):
                with patch("nitrostack.auth.cimd._fetch_cimd_bytes", return_value=body):
                    doc = await resolve_cimd(url)
                    assert doc["client_name"] == "NitroStudio"

        asyncio.run(_run())


    def test_fetch_connects_to_pinned_ip(self):
        connected: dict[str, object] = {}

        def fake_create(address, timeout=None):
            connected["addr"] = address
            raise OSError("stop before handshake")

        with patch("nitrostack.auth.cimd.socket.create_connection", side_effect=fake_create):
            with pytest.raises(CimdFetchError):
                _fetch_cimd_bytes(
                    "https://app.nitrostack.io/oauth/client-metadata.json",
                    timeout_sec=1.0,
                    pinned_ip="8.8.8.8",
                )
        assert connected["addr"][0] == "8.8.8.8"


class TestCimdTrustedProxyHost:
    def test_direct_peer_must_match_pin(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        assert cimd_peer_is_acceptable("8.8.8.8", "8.8.8.8") is True
        assert cimd_peer_is_acceptable("1.2.3.4", "8.8.8.8") is False

    def test_trusted_proxy_peer_is_acceptable(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        assert cimd_peer_is_acceptable("10.0.0.5", "8.8.8.8") is True
        assert cimd_peer_is_acceptable("10.0.0.9", "8.8.8.8") is False

    def test_untrusted_forwarded_host_cannot_rebind_cimd(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        headers = {
            "Host": "mcp.nitrostack.io",
            "X-Forwarded-Host": "evil.example",
        }
        url = "https://evil.example/oauth/client-metadata.json"
        assert request_host_for_cimd(headers, peer="8.8.8.8") == "mcp.nitrostack.io"
        assert cimd_host_matches_request(url, headers, peer="8.8.8.8") is False

    def test_apply_cimd_records_untrusted_request_host(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        url = "https://app.nitrostack.io/oauth/client-metadata.json"
        headers = {
            "Host": "mcp.nitrostack.io",
            "X-Forwarded-Host": "app.nitrostack.io",
        }
        with patch(
            "nitrostack.auth.oauth_module.resolve_cimd_sync",
            return_value={"client_id": url, "client_name": "Studio"},
        ):
            body = apply_cimd_to_registration_body(
                {"client_id": url},
                headers=headers,
                peer="8.8.8.8",
            )
        assert body["_cimd_request_host"] == "mcp.nitrostack.io"
        assert body["_cimd_host_matches_request"] is False

    def test_apply_cimd_honors_trusted_forwarded_host(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
        url = "https://app.nitrostack.io/oauth/client-metadata.json"
        headers = {
            "Host": "internal:3000",
            "X-Forwarded-Host": "app.nitrostack.io",
        }
        with patch(
            "nitrostack.auth.oauth_module.resolve_cimd_sync",
            return_value={"client_id": url, "client_name": "Studio"},
        ):
            body = apply_cimd_to_registration_body(
                {"client_id": url},
                headers=headers,
                peer="10.0.0.5",
            )
        assert body["_cimd_request_host"] == "app.nitrostack.io"
        assert body["_cimd_host_matches_request"] is True


class TestCimdRegistrationWiring:
    def test_applies_cimd_document_to_registration_body(self):
        url = "https://app.nitrostack.io/oauth/client-metadata.json"
        with patch(
            "nitrostack.auth.oauth_module.resolve_cimd_sync",
            return_value={"client_id": url, "client_name": "Studio"},
        ):
            body = apply_cimd_to_registration_body({"client_id": url, "redirect_uris": []})
        assert body["client_id"] == url
        assert body["_cimd"]["client_name"] == "Studio"

    def test_leaves_non_url_client_id_unchanged(self):
        body = apply_cimd_to_registration_body({"client_id": "static-client"})
        assert body["client_id"] == "static-client"
        assert "_cimd" not in body


class TestProtectedResourceMetadata:
    def test_includes_bearer_methods_supported(self):
        metadata = build_protected_resource_metadata(_StubOAuthService())
        assert metadata["resource"] == "https://mcp.nitrostack.io/mcp"
        assert metadata["authorization_servers"] == ["https://auth.nitrostack.io"]
        assert metadata["bearer_methods_supported"] == ["header"]


class TestRfc9207IssValidation:
    def test_accepts_matching_issuer(self):
        validate_authorization_iss("https://auth.nitrostack.io/", "https://auth.nitrostack.io")

    def test_rejects_missing_iss(self):
        with pytest.raises(AuthorizationIssuerMismatchError, match="missing"):
            validate_authorization_iss(None, "https://auth.nitrostack.io")

    def test_rejects_mismatched_iss(self):
        with pytest.raises(AuthorizationIssuerMismatchError, match="mismatch"):
            validate_authorization_iss("https://evil.example", "https://auth.nitrostack.io")
