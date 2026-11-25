"""Runtime ports, environment settings and npm launch helpers."""

import os
import sys
import shutil
import subprocess


def _normalize_port(value, label):
    try:
        port = int(value)
    except (TypeError, ValueError):
        print(f"Error: Invalid {label} '{value}'.")
        sys.exit(1)
    if port < 1 or port > 65535:
        print(f"Error: {label} {port} is out of range (1-65535).")
        sys.exit(1)
    return str(port)

def _read_project_env():
    values = {}
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return values
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception:
        pass
    return values

def _resolve_runtime_ports(
    port = None,
    widget = None,
    *,
    DEFAULT_MCP_PORT,
    DEFAULT_WIDGETS_PORT,
    _normalize_port,
    _read_project_env,
):
    """Resolve MCP and widget ports. Explicit --port/--widget flags override defaults."""
    project_env = _read_project_env()
    if port is not None:
        mcp_port = _normalize_port(port, "--port")
    else:
        mcp_port = (
            os.environ.get("PORT")
            or os.environ.get("MCP_SERVER_PORT")
            or project_env.get("PORT")
            or project_env.get("MCP_SERVER_PORT")
            or DEFAULT_MCP_PORT
        )
        mcp_port = str(mcp_port)
        if widget is None and mcp_port == DEFAULT_WIDGETS_PORT:
            mcp_port = DEFAULT_MCP_PORT

    if widget is not None:
        widgets_port = _normalize_port(widget, "--widget")
    else:
        widgets_port = os.environ.get("WIDGETS_DEV_PORT") or project_env.get("WIDGETS_DEV_PORT") or DEFAULT_WIDGETS_PORT
        widgets_port = str(widgets_port)
        if port is None and widgets_port == DEFAULT_MCP_PORT:
            widgets_port = DEFAULT_WIDGETS_PORT
    return mcp_port, widgets_port

def _upsert_env_var(lines, key, value):
    prefix = f"{key}="
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}\n")
    return new_lines

def _find_npm():
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError(
            "npm was not found on PATH. Install Node.js (https://nodejs.org) and retry."
        )
    return npm

def _run_npm(args, *, _find_npm, **kwargs):
    """Run npm without shell=True. A list + shell=True is treated as `sh -c npm` and drops flags."""
    kwargs.pop("shell", None)
    return subprocess.run([_find_npm(), *args], **kwargs)

def _popen_npm(args, *, _find_npm, **kwargs):
    kwargs.pop("shell", None)
    return subprocess.Popen([_find_npm(), *args], **kwargs)

def _add_port_flags(parser, *, DEFAULT_MCP_PORT, DEFAULT_WIDGETS_PORT):
    parser.add_argument(
        "--port",
        default=None,
        help=f"MCP HTTP port (default: {DEFAULT_MCP_PORT}; overrides PORT when set)",
    )
    parser.add_argument(
        "--widget",
        default=None,
        help=f"Widget dev server port (default: {DEFAULT_WIDGETS_PORT}; overrides WIDGETS_DEV_PORT when set)",
    )
