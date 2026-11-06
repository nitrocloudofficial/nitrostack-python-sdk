"""Application configuration, decorator, and startup metadata helpers."""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Type

import mcp.types as types

from nitrostack.protocol.version import (
    MODERN_PROTOCOL_VERSION,
)

logger = logging.getLogger(__name__)

DEFAULT_HTTP_PORT = 3000


def _iso_timestamp(value: Any) -> str:
    """Official Task timestamps are ISO-8601 strings."""
    if hasattr(value, "isoformat"):
        text = value.isoformat()
        if text.endswith("+00:00"):
            return text[:-6] + "Z"
        return text
    return str(value)


class GetTaskResult(types.GetTaskResult):
    """Official GetTaskResult plus NitroStack-owned ``result`` / ``error`` slots."""

    result: Any = None
    error: Any = None


def resolve_http_port() -> int:
    """MCP HTTP/dual bind port. Defaults to 3000; 3001 is reserved for widgets."""
    return int(os.environ.get("PORT") or os.environ.get("MCP_SERVER_PORT") or DEFAULT_HTTP_PORT)


def resolve_http_host() -> str:
    """Bind host. ``HOST`` matches the TypeScript templates; default is ``127.0.0.1``."""
    return (os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1"


def _read_dotenv_value(root: str, key: str) -> Optional[str]:
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() != key:
                    continue
                return value.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def _project_identity(root: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Name / version / description for the `GET /` documentation page."""
    cwd = root or os.getcwd()
    name = os.environ.get("SERVER_NAME") or _read_dotenv_value(cwd, "SERVER_NAME")
    description = os.environ.get("SERVER_DESC") or _read_dotenv_value(cwd, "SERVER_DESC")
    version = None
    path = os.path.join(cwd, "pyproject.toml")
    if os.path.isfile(path):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            text = ""
        if not name:
            match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', text)
            name = match.group(1) if match else None
        if not description:
            match = re.search(r'(?m)^description\s*=\s*"([^"]+)"', text)
            description = match.group(1) if match else None
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        version = match.group(1) if match else None
    if not name:
        name = os.path.basename(os.path.abspath(cwd)) or None
    return {"name": name, "version": version, "description": description}


def _apply_project_identity(config: "ServerConfig") -> None:
    ident = _project_identity()
    generic = (config.name or "").strip().lower() in {"", "app", "mcp-server"}
    if generic and ident.get("name"):
        config.name = ident["name"]
    if not config.description and ident.get("description"):
        config.description = ident["description"]


@dataclass
class ServerConfig:
    name: str
    version: str = "1.0.0"
    transport_type: Optional[Literal["stdio", "http", "dual"]] = None
    protocol_version: str = MODERN_PROTOCOL_VERSION
    # Era fallback when MCP_STATELESS and NITRO_MCP_PROTOCOL_VERSION are unset.
    # Same tokens as the env var (modern/latest/2026/2026-07-28, auto/both/dual/
    # dual-spec, legacy/2025/2025-06-18/2025-11-25). None means default era (`auto`).
    protocol_era: Optional[str] = None
    # Streamable HTTP options. Era resolution at `start()` / `get_combined_app()`:
    # `MCP_STATELESS`, then `NITRO_MCP_PROTOCOL_VERSION`, then `protocol_era`,
    # then `auto`. Also: `ENABLE_CORS`, `MCP_MAX_SESSIONS`,
    # `MCP_SESSION_TIMEOUT_MS`, `MCP_TRANSPORT_TYPE`.
    # `MCP_STATELESS` wins over the protocol-era mapping. Unset era is `auto`
    # and does not force this flag; only `modern` sets stateless HTTP.
    stateless: bool = False
    max_sessions: Optional[int] = None
    session_timeout_ms: Optional[int] = None
    json_response: bool = False
    extensions: Optional[Dict[str, str]] = None
    # Subtitle on the `GET /` documentation page.
    description: Optional[str] = None


def mcp_app(
    module: Type,
    server: ServerConfig,
    transforms: Optional[List[Any]] = None,
):
    """Declare an MCP application and its optional tool-list transforms."""

    def decorator(cls: Type):
        cls._mcp_app_module = module
        cls._mcp_app_server = server
        cls._mcp_transforms = list(transforms or [])
        return cls

    return decorator
