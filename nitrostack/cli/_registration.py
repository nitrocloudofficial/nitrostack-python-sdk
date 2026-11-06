"""Claude Desktop and Cursor client registration."""

import os
import sys


def get_claude_config_paths():
    paths = []
    home = os.path.expanduser("~")
    
    # Windows
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
        # Windows Store app path
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            store_dir = os.path.join(localappdata, "Packages")
            if os.path.exists(store_dir):
                try:
                    for folder in os.listdir(store_dir):
                        if folder.startswith("Claude_"):
                            paths.append(os.path.join(store_dir, folder, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
                except Exception:
                    pass
    # macOS
    elif sys.platform == "darwin":
        paths.append(os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json"))
    # Linux
    else:
        paths.append(os.path.join(home, ".config", "Claude", "claude_desktop_config.json"))
        
    return [p for p in paths if os.path.exists(os.path.dirname(p))]

def register_server(name: str, file_path: str, *, _build_server_entry, get_claude_config_paths):
    import json
    
    if not os.path.exists(file_path):
        print(f"Error: Script file '{file_path}' does not exist.")
        sys.exit(1)
        
    server_entry = _build_server_entry(file_path)
    
    config_paths = get_claude_config_paths()
    if not config_paths:
        print("Error: Could not find any Claude Desktop installation directories.")
        print("Please ensure Claude Desktop is installed on your machine.")
        sys.exit(1)
        
    registered_any = False
    for path in config_paths:
        try:
            config = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        try:
                            config = json.loads(content)
                        except json.JSONDecodeError:
                            print(f"Warning: Configuration file at '{path}' is not valid JSON. Resetting it.")
            
            if "mcpServers" not in config:
                config["mcpServers"] = {}
                
            config["mcpServers"][name] = server_entry
            
            # Create directory if needed
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                
            print(f"Successfully registered server '{name}' in: {path}")
            registered_any = True
        except Exception as e:
            print(f"Error writing to config at '{path}': {e}")
            
    if registered_any:
        print("\nAll done! Please fully restart Claude Desktop to load your new server.")
    else:
        print("Error: Failed to register the server in any configuration files.")

def _build_server_entry(file_path: str):
    """Build the stdio server entry shared by register and Cursor configs."""
    return {
        "command": sys.executable,
        "args": [os.path.abspath(file_path)],
    }

def _write_json_atomic(path: str, data):
    """Replace a JSON config atomically so readers never see a partial write."""
    import json
    import tempfile

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".mcp-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

def register_cursor_server(
    name: str,
    file_path: str = 'main.py',
    *,
    global_config: bool = False,
    transport: str = 'command',
    _build_server_entry,
    _write_json_atomic,
):
    """Merge this project into Cursor's project or user MCP configuration."""
    import json

    if transport not in ("command", "streamable-http", "sse"):
        raise ValueError("transport must be command, streamable-http, or sse")
    if transport == "command" and not os.path.exists(file_path):
        print(f"Error: Script file '{file_path}' does not exist.")
        sys.exit(1)

    if global_config:
        config_path = os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json")
    else:
        config_path = os.path.join(os.getcwd(), ".cursor", "mcp.json")

    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            config = {}
    if not isinstance(config, dict):
        config = {}
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers

    if transport == "command":
        entry = _build_server_entry(file_path)
    else:
        route = "mcp" if transport == "streamable-http" else "sse"
        entry = {"url": f"http://localhost:3000/{route}"}
    servers[name] = entry
    _write_json_atomic(config_path, config)
    print(f"Successfully registered server '{name}' in Cursor config: {config_path}")
