"""
`GET /` documentation page, ported from the TypeScript SDK's
`StreamableHttpTransport.generateDocumentationPage`.

The markup lives in `assets/landing.html` with `{{placeholder}}` tokens.
A project can override the header logo with `assets/logo.png` or
`src/assets/logo.png` in the working directory.
"""
from __future__ import annotations

import base64
import functools
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_ASSETS = Path(__file__).parent / "assets"
_PROJECT_LOGO_PATHS = ("assets/logo.png", "src/assets/logo.png")
DEFAULT_DESCRIPTION = "A powerful MCP server built with NitroStack"

_SCHEMA_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" '
    'stroke="currentColor" style="width:0.9rem;height:0.9rem;">'
    '<path stroke-linecap="round" stroke-linejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 '
    '0L1.5 12l5.25-5.25m7.5-3-4.5 16.5" /></svg>'
)


@functools.lru_cache(maxsize=1)
def _template() -> str:
    return (_ASSETS / "landing.html").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _default_logo() -> str:
    return base64.b64encode((_ASSETS / "logo.png").read_bytes()).decode("ascii")


def _logo_base64() -> str:
    cwd = Path(os.getcwd())
    for rel in _PROJECT_LOGO_PATHS:
        path = cwd / rel
        if path.is_file():
            try:
                return base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
    return _default_logo()


def format_title(name: str) -> str:
    if "/" in name or "\\" in name:
        return name
    return " ".join(word[:1].upper() + word[1:] for word in re.split(r"[-_]+", name))


def _tools_section(tools: List[Dict[str, Any]]) -> str:
    if not tools:
        return (
            '<div class="empty-state">\n'
            "        <p>No tools are currently registered on this server.</p>\n"
            "      </div>"
        )
    cards = []
    for idx, tool in enumerate(tools):
        name = html.escape(tool.get("name") or "")
        desc = tool.get("description") or ""
        schema_btn = (
            f'<button class="schema-toggle" onclick="openToolModal({idx})">'
            f"{_SCHEMA_ICON}<span>View Input Schema</span></button>"
            if tool.get("inputSchema")
            else ""
        )
        cards.append(
            f'<div class="tool-card" data-name="{name.lower()}" '
            f'data-desc="{html.escape(desc).lower()}">\n'
            f'  <div class="tool-header"><div class="tool-name-container">'
            f'<span class="tool-name">{name}</span></div></div>\n'
            f'  <p class="tool-description">{html.escape(desc or "No description available")}</p>\n'
            f"  {schema_btn}\n"
            "</div>"
        )
    return '<div class="tools-grid" id="tools-container">\n' + "\n".join(cards) + "\n</div>"


def _script_json(value: Any) -> str:
    """JSON safe to inline inside `<script>` (no `</script>` breakout)."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_landing_page(
    *,
    name: str,
    version: str,
    mcp_endpoint: str,
    tools: List[Dict[str, Any]],
    description: Optional[str] = None,
) -> str:
    title = format_title(name)
    values = {
        "logo_base64": _logo_base64(),
        "server_slug": html.escape(re.sub(r"\s+", "-", title.lower())),
        "server_name": html.escape(title),
        "server_version": html.escape(version),
        "server_description": html.escape(description or DEFAULT_DESCRIPTION),
        "mcp_endpoint": html.escape(mcp_endpoint),
        "tools_section": _tools_section(tools),
        "tools_json": _script_json(tools),
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), _template())
