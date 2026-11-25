"""NitroStack app mode — OpenAI Apps SDK vs MCP Apps wire conventions."""

from __future__ import annotations

import os
from typing import Literal

AppMode = Literal["openai", "mcp-app", "universal"]

RESOURCE_MIME_TYPE_MCP_APP = "text/html;profile=mcp-app"
RESOURCE_MIME_TYPE_OPENAI = "text/html"
OPENAI_SKYBRIDGE_MIME_TYPE = "text/html+skybridge"


def get_app_mode() -> AppMode:
    """Parse ``NITROSTACK_APP_MODE`` / ``APP_MODE`` with lenient aliases.

    Default is ``openai``. ``NITROSTACK_APP_MODE`` wins when both are set.
    """
    raw = os.environ.get("NITROSTACK_APP_MODE") or os.environ.get("APP_MODE")
    if raw is None:
        return "openai"
    raw = raw.lower().strip()
    if not raw:
        return "openai"
    if raw in ("openai", "chatgpt"):
        return "openai"
    if raw in ("mcp-app", "mcpapp", "mcp_app", "mcp app"):
        return "mcp-app"
    if raw in ("universal", "all", "both"):
        return "universal"
    return "openai"


def is_mcp_app_mode() -> bool:
    mode = get_app_mode()
    return mode in ("mcp-app", "universal")


def is_openai_mode() -> bool:
    mode = get_app_mode()
    return mode in ("openai", "universal")


def get_widget_mime_type() -> str:
    """MIME for ``resources/read`` widget HTML (never skybridge)."""
    return RESOURCE_MIME_TYPE_MCP_APP if is_mcp_app_mode() else RESOURCE_MIME_TYPE_OPENAI