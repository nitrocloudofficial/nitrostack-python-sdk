"""NITROSTACK_APP_MODE / APP_MODE parsing (mirrors TS core app-mode tests)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.core.app_mode import (
    RESOURCE_MIME_TYPE_MCP_APP,
    RESOURCE_MIME_TYPE_OPENAI,
    get_app_mode,
    get_widget_mime_type,
    is_mcp_app_mode,
    is_openai_mode,
)


@pytest.fixture(autouse=True)
def _clean_app_mode_env(monkeypatch):
    monkeypatch.delenv("NITROSTACK_APP_MODE", raising=False)
    monkeypatch.delenv("APP_MODE", raising=False)
    yield


def test_default_mode_is_openai():
    assert get_app_mode() == "openai"
    assert is_mcp_app_mode() is False
    assert is_openai_mode() is True
    assert get_widget_mime_type() == RESOURCE_MIME_TYPE_OPENAI


def test_defaults_openai_for_blank_or_unknown(monkeypatch):
    monkeypatch.setenv("NITROSTACK_APP_MODE", "   ")
    assert get_app_mode() == "openai"
    monkeypatch.setenv("NITROSTACK_APP_MODE", "unknown-flavor")
    assert get_app_mode() == "openai"


def test_openai_aliases(monkeypatch):
    monkeypatch.setenv("NITROSTACK_APP_MODE", "  OPENAI  ")
    assert get_app_mode() == "openai"
    monkeypatch.setenv("NITROSTACK_APP_MODE", "chatgpt")
    assert get_app_mode() == "openai"
    monkeypatch.setenv("APP_MODE", "openai")
    assert get_app_mode() == "openai"


@pytest.mark.parametrize(
    "alias",
    ["mcp-app", "mcpapp", "mcp_app", "mcp app", "mcp", "mcpapps", "MCP-APP"],
)
def test_mcp_app_aliases(monkeypatch, alias):
    monkeypatch.setenv("NITROSTACK_APP_MODE", alias)
    assert get_app_mode() == "mcp-app"
    assert is_mcp_app_mode() is True
    assert is_openai_mode() is False
    assert get_widget_mime_type() == RESOURCE_MIME_TYPE_MCP_APP


def test_mcp_app_via_app_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "mcp-app")
    assert get_app_mode() == "mcp-app"


@pytest.mark.parametrize("alias", ["universal", "UNIVERSAL", "all", "both"])
def test_universal_aliases(monkeypatch, alias):
    monkeypatch.setenv("NITROSTACK_APP_MODE", alias)
    assert get_app_mode() == "universal"
    assert is_mcp_app_mode() is True
    assert is_openai_mode() is True
    assert get_widget_mime_type() == RESOURCE_MIME_TYPE_MCP_APP


def test_nitrostack_app_mode_wins_over_app_mode(monkeypatch):
    monkeypatch.setenv("NITROSTACK_APP_MODE", "openai")
    monkeypatch.setenv("APP_MODE", "mcp-app")
    assert get_app_mode() == "openai"