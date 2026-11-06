from nitrostack.cli._config import DEFAULT_PROJECT_NAME, DEFAULT_MCP_PORT, DEFAULT_WIDGETS_PORT, OFFICIAL_TEMPLATES
from nitrostack.cli._template_text import (
    MAIN_TEMPLATE,
    APP_MODULE_TEMPLATE,
    CALC_MODULE_TEMPLATE,
    CALC_SERVICE_TEMPLATE,
    CALC_TOOLS_TEMPLATE,
    FOOD_APP_MODULE_TEMPLATE,
    FOOD_DELIVERY_MODULE_TEMPLATE,
    FOOD_DELIVERY_SERVICE_TEMPLATE,
    FOOD_DELIVERY_TOOLS_TEMPLATE,
    FLIGHT_APP_MODULE_TEMPLATE,
    FLIGHT_OAUTH_GUARD_TEMPLATE,
    FLIGHT_SYSTEM_HEALTH_TEMPLATE,
    FLIGHT_DUFFEL_SERVICE_TEMPLATE,
    FLIGHT_MODULE_TEMPLATE,
    FLIGHT_TOOLS_TEMPLATE,
    FLIGHT_BOOKING_TOOLS_TEMPLATE,
    FLIGHT_PROMPTS_TEMPLATE,
    FLIGHT_RESOURCES_TEMPLATE,
    OAUTH_SETUP_TEMPLATE,
    ENV_TEMPLATE,
    REQUIREMENTS_TEMPLATE,
    TOOL_TEMPLATE,
    WIDGET_PREVIEW_HTML,
    SAMPLE_PIZZA_PREVIEW_JSON,
    MODULE_TEMPLATE,
)

import os
import re
import sys
import argparse
import shutil
import subprocess
import time
from pathlib import Path

from nitrostack.cli.generate import generate_component, generate_module as generate_module_from_template
from nitrostack.cli.install import install_dependencies, lock_with_uv, rewrite_pyproject_identity
from nitrostack.cli.pack import pack_project
from nitrostack.cli.skills import run_skills_flow
from nitrostack.cli.upgrade import upgrade_project
from nitrostack.cli.validators import format_report, validate_project






















# Official CLI names → on-disk template directories.






_GENERATE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WIDGET_ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_generate_name(name: str) -> str:
    """Reject path segments so ``generate tool ../../ESCAPED`` cannot write outside cwd."""
    name = (name or "").strip()
    if not name or os.path.basename(name) != name or ".." in name or not _GENERATE_NAME_RE.fullmatch(name):
        raise ValueError(
            "name must be a Python identifier (letters, digits, underscore) "
            "and cannot contain path separators"
        )
    return name


def _sanitize_widget_route(route: str) -> str:
    route = (route or "").strip()
    if not route or os.path.basename(route) != route or ".." in route or not _WIDGET_ROUTE_RE.fullmatch(route):
        raise ValueError("widget route must be a single alphanumeric path segment")
    return route


def _assert_dest_inside_root(dest: str, root: str) -> str:
    dest_abs = os.path.realpath(dest)
    root_abs = os.path.realpath(root)
    try:
        common = os.path.commonpath([dest_abs, root_abs])
    except ValueError as exc:
        raise ValueError("generated path would escape the project directory") from exc
    if common != root_abs:
        raise ValueError("generated path would escape the project directory")
    return dest_abs


def write_widget_html(project_dir: str, route: str, *, overwrite: bool = False) -> str:
    """Write ``widgets/out/{route}.html`` if missing (Python-only static widget)."""
    from nitrostack.widgets.route_templates import build_widget_html_for_route

    route = _sanitize_widget_route(route)
    project_dir = os.path.abspath(project_dir or ".")
    out_dir = os.path.join(project_dir, "widgets", "out")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{route}.html")
    _assert_dest_inside_root(dest, project_dir)
    if overwrite or not os.path.exists(dest):
        with open(dest, "w", encoding="utf-8") as f:
            f.write(build_widget_html_for_route(route))
    return dest


def write_widget_preview(project_dir: str) -> None:
    from html import escape as html_escape

    from nitrostack.widgets.html_util import json_for_inline_script

    out_dir = os.path.join(project_dir, "widgets", "out")
    if not os.path.isdir(out_dir):
        return
    routes = sorted(p[:-5] for p in os.listdir(out_dir) if p.endswith(".html"))
    if not routes:
        return
    first = "pizza-list" if "pizza-list" in routes else routes[0]
    default_payload: dict = {"status": "success"}
    if "pizza-list" in routes or "pizza-map" in routes:
        import json as json_lib

        default_payload = json_lib.loads(SAMPLE_PIZZA_PREVIEW_JSON)
    html = (
        WIDGET_PREVIEW_HTML.replace("__FIRST__", html_escape(first, quote=True))
        .replace("__ROUTES__", json_for_inline_script(routes))
        .replace("__DEFAULT_JSON__", html_escape(json_for_inline_script(default_payload), quote=False))
    )
    dest = os.path.join(project_dir, "widgets", "preview.html")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)


def ensure_python_widgets(project_dir: str) -> list:
    """Create missing ``widgets/out/{route}.html`` for every ``@widget`` in the project."""
    routes = []
    for root, _dirs, files in os.walk(project_dir):
        parts = set(root.split(os.sep))
        if "node_modules" in parts or ".venv" in parts:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                continue
            routes.extend(re.findall(r'@widget\(\s*["\']([^"\']+)["\']', text))
            routes.extend(re.findall(r'WidgetOptions\(\s*route\s*=\s*["\']([^"\']+)["\']', text))
    # Only routes that were actually scaffolded go into the returned list — callers
    # print it as "created", so appending before the write would report a widget that
    # never landed on disk. A rejected route is surfaced rather than skipped silently.
    unique = []
    seen = set()
    for route in routes:
        if route in seen:
            continue
        seen.add(route)
        try:
            write_widget_html(project_dir, route, overwrite=False)
        except ValueError as exc:
            print(f"Warning: skipped widget route {route!r}: {exc}")
            continue
        unique.append(route)
    write_widget_preview(project_dir)
    return unique


def print_banner():
    banner = """\033[34m╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   _   _  ___ _____ ____   ___                            ║
║  | \\ | ||_ _|_   _|  _ \\ / _ \\                           ║
║  |  \\| | | |  | | | |_) | | | |                          ║
║  | |\\  | | |  | | |  _ <| |_| |                          ║
║  |_| \\_||___| |_| |_| \\_\\\\___/                           ║
║                                                          ║
║   \033[1;34mNITROSTACK\033[0;34m — Official MCP Framework                   ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝\033[0m"""
    print(banner)

def _stdin_is_interactive() -> bool:
    """False for Studio/CI, which inherit an stdin that may never write."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _prompt(message: str, default: str = "") -> str:
    default_hint = f" [{default}]" if default else ""
    sys.stdout.write(f"\033[32m? \033[1;37m{message}:\033[0m{default_hint} ")
    if not _stdin_is_interactive():
        sys.stdout.write(f"{default}\n")
        sys.stdout.flush()
        return default
    sys.stdout.flush()
    try:
        value = sys.stdin.readline().strip()
    except Exception:
        value = ""
    return value or default


def _resolve_template(template: str) -> str:
    key = (template or "").strip().lower()
    if key not in OFFICIAL_TEMPLATES:
        names = ", ".join(OFFICIAL_TEMPLATES)
        print(f"Error: Unknown template '{template}'. Use one of: {names}")
        sys.exit(1)
    return key


def _prompt_template() -> str:
    print("\033[32m? \033[1;37mChoose a template:\033[0m")
    print("  \033[34m1. python-starter\033[0m     Starter — simple calculator for learning basics")
    print("  \033[34m2. python-pizzaz\033[0m      Advanced — pizza shop finder with maps & widgets")
    print("  \033[34m3. python-oauth\033[0m       Flight booking with OAuth 2.1 auth")
    print("  \033[34m4. python-enterprise-search\033[0m  BM25 progressive tool discovery")
    print("  \033[34m5. python-code-mode\033[0m         QuickJS multi-tool workflows")
    by_choice = {
        "1": "python-starter",
        "2": "python-pizzaz",
        "3": "python-oauth",
        "4": "python-enterprise-search",
        "5": "python-code-mode",
    }
    while True:
        choice = _prompt("Enter choice (1-5) or template name", "1")
        if choice in by_choice:
            return by_choice[choice]
        if choice.lower() in OFFICIAL_TEMPLATES:
            return choice.lower()
        print("Please enter 1-5, or an explicit template name (" + ", ".join(OFFICIAL_TEMPLATES) + ").")


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


def _resolve_runtime_ports(port=None, widget=None):
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


def _prompt_yes_no(message: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    sys.stdout.write(f"\033[32m? \033[1;37m{message}:\033[0m ({hint}) ")
    if not _stdin_is_interactive():
        sys.stdout.write(("yes" if default_yes else "no") + "\n")
        sys.stdout.flush()
        return default_yes
    sys.stdout.flush()
    try:
        ans = sys.stdin.readline().strip().lower()
    except Exception:
        ans = ""
    if not ans:
        return default_yes
    if ans in ("y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return default_yes


def _find_npm():
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError(
            "npm was not found on PATH. Install Node.js (https://nodejs.org) and retry."
        )
    return npm


def _run_npm(args, **kwargs):
    """Run npm without shell=True. A list + shell=True is treated as `sh -c npm` and drops flags."""
    kwargs.pop("shell", None)
    return subprocess.run([_find_npm(), *args], **kwargs)


def _popen_npm(args, **kwargs):
    kwargs.pop("shell", None)
    return subprocess.Popen([_find_npm(), *args], **kwargs)


def _add_port_flags(parser):
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


def init_project(name: str = None, template: str = None, skip_install: bool = False, port=None, widget=None, force: bool = False):
    print_banner()

    # 1. Project name — optional CLI arg, otherwise the next readline
    if not name:
        name = _prompt("Project name", DEFAULT_PROJECT_NAME)
    name = (name or "").strip()
    if not name:
        print("Error: Project name cannot be empty.")
        sys.exit(1)

    # 2. Overwrite check
    if os.path.exists(name):
        if not _stdin_is_interactive():
            print(f"Error: Directory '{name}' already exists.")
            sys.exit(1)
        sys.stdout.write(f"\033[32m? \033[1;37mDirectory '{name}' already exists. Overwrite?\033[0m (Yes/No) [No]: ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans not in ("y", "yes"):
            print("Initialization cancelled.")
            sys.exit(0)
        shutil.rmtree(name, ignore_errors=True)

    # 3. Select template (explicit python-* names)
    if not template:
        template = _prompt_template()
    else:
        template = _resolve_template(template)

    # 4. Description and Author (defaults when stdin is not a TTY)
    description = _prompt("Description", "My awesome MCP server")
    author = _prompt("Author", "developer")

    # 5. Install dependencies (Y/n). --skip-install skips the prompt.
    if skip_install:
        install_deps = False
    else:
        install_deps = _prompt_yes_no("Install dependencies", default_yes=True)

    mcp_port = _normalize_port(port, "--port") if port is not None else DEFAULT_MCP_PORT
    widgets_port = _normalize_port(widget, "--widget") if widget is not None else DEFAULT_WIDGETS_PORT

    # 6. Copy template directory
    import nitrostack
    package_dir = os.path.dirname(nitrostack.__file__)
    template_dir = OFFICIAL_TEMPLATES[template]
    template_src_dir = os.path.join(package_dir, "templates", template_dir)

    if not os.path.exists(template_src_dir):
        print(f"Error: Template '{template}' not found at '{template_src_dir}'.")
        sys.exit(1)

    shutil.copytree(template_src_dir, name)
    rewrite_pyproject_identity(
        os.path.join(name, "pyproject.toml"),
        name=os.path.basename(os.path.abspath(name)),
        description=description,
    )
    from nitrostack.cli.vendor import ensure_vendored_nitrostack
    ensure_vendored_nitrostack(name)
    lock_with_uv(name)
    widget_routes = ensure_python_widgets(name)
    print("\n\033[32m✓\033[0m Project created")
    if widget_routes:
        print(f"\033[32m✓\033[0m Python widgets: {', '.join(widget_routes)}")
        print("    HTML in widgets/out/ — preview: widgets/preview.html")

    # 7. Update .env file
    env_path = os.path.join(name, ".env")
    env_example_path = os.path.join(name, ".env.example")
    if not os.path.exists(env_path) and os.path.isfile(env_example_path):
        shutil.copyfile(env_example_path, env_path)
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith("SERVER_NAME="):
                new_lines.append(f'SERVER_NAME="{os.path.basename(os.path.abspath(name))}"\n')
            elif line.startswith("SERVER_DESC="):
                new_lines.append(f'SERVER_DESC="{description}"\n')
            elif line.startswith("SERVER_AUTHOR="):
                new_lines.append(f'SERVER_AUTHOR="{author}"\n')
            else:
                new_lines.append(line)
        has_name = any(line.startswith("SERVER_NAME=") for line in new_lines)
        has_desc = any(line.startswith("SERVER_DESC=") for line in new_lines)
        has_author = any(line.startswith("SERVER_AUTHOR=") for line in new_lines)
        if not has_name:
            new_lines.append(f'SERVER_NAME="{os.path.basename(os.path.abspath(name))}"\n')
        if not has_desc:
            new_lines.append(f'SERVER_DESC="{description}"\n')
        if not has_author:
            new_lines.append(f'SERVER_AUTHOR="{author}"\n')
        new_lines = _upsert_env_var(new_lines, "PORT", mcp_port)
        new_lines = _upsert_env_var(new_lines, "WIDGETS_DEV_PORT", widgets_port)
        new_lines = _upsert_env_var(new_lines, "NITROSTACK_APP_MODE", "universal")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    # 8. Update widgets package.json
    widgets_package_path = os.path.join(name, "src", "widgets", "package.json")
    if os.path.exists(widgets_package_path):
        import json
        try:
            with open(widgets_package_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            pkg["name"] = f"{name}-widgets"
            # The port is passed once by the CLI (`npm run dev -- --port N`), so it
            # is deliberately not baked into the script.
            scripts = pkg.setdefault("scripts", {})
            scripts["dev"] = "next dev"
            scripts["start"] = "next start"
            with open(widgets_package_path, "w", encoding="utf-8") as f:
                json.dump(pkg, f, indent=2)
        except Exception:
            pass

    # 9. Install Python deps (requirements.txt / pyproject.toml), then widget npm if present.
    if install_deps:
        print("Installing dependencies...")
        try:
            install_dependencies(cwd=name)
            print("\033[32m✓\033[0m Dependencies installed")
        except Exception as e:
            print(f"Warning: Failed to install dependencies: {e}")
            print("Please run 'nitrostack-py install' from the project directory.\n")
        widgets_dir = os.path.join(name, "src", "widgets")
        if os.path.exists(widgets_dir):
            print("Installing widget dependencies...")
            try:
                _run_npm(["--version"], capture_output=True, check=True, text=True)
                _run_npm(["install"], cwd=widgets_dir, check=True)
                print("\033[32m✓\033[0m Widget dependencies installed\n")
            except FileNotFoundError as e:
                print(f"Warning: {e}")
                print("Please run 'npm install' inside 'src/widgets' manually.\n")
            except subprocess.CalledProcessError as e:
                detail = (getattr(e, "stderr", None) or getattr(e, "stdout", None) or str(e)).strip()
                print(f"Warning: Failed to install widget dependencies: {detail}")
                print("Please run 'npm install' inside 'src/widgets' manually.\n")
    else:
        print("\033[32m✓\033[0m Skipped dependency install")

    run_skills_flow(os.path.abspath(name), force=force)

    # Success Card
    abs_path = os.path.abspath(name)
    success_box = f"""\033[36m╔══════════════════════════════════════════════════════════╗
║ \033[32m✓ Project Ready\033[36m                                          ║
║                                                          ║
║   Name: {name:<48} ║
║   Template: {template:<44} ║
║   Path: {abs_path:<48} ║
╚══════════════════════════════════════════════════════════╝\033[0m"""
    print(success_box)

    # Next Steps
    print("\n\033[1;37mNext steps:\033[0m")
    print(f" 1. \033[34mcd {name}\033[0m")
    if template in ("python-enterprise-search", "python-code-mode"):
        print(" 2. Read README.md for sample tool calls and local demonstration data")
        print(" 3. Validate the project: nitrostack-py validate")
        print(" 4. Install dependencies if skipped: nitrostack-py install")
        print(" 5. Start an MCP stdio server: python main.py")
        print("    For HTTP set MCP_TRANSPORT_TYPE=http, then connect to http://localhost:3000/mcp")
        if template == "python-code-mode":
            print("    Try scripts/sample-batch.js through execute; QuickJS must be installed")
        else:
            print("    Try search_tools with 'overdue invoices', then invoke it through call_tool")
        print("\nHappy coding! 🚀\n")
        return
    if template == "python-oauth":
        print(" 2. Configure OAuth credentials in your \033[34m.env\033[0m file")
        print("    See \033[34mOAUTH_SETUP.md\033[0m for provider guides")
    else:
        print(" 2. Configure environment variables in \033[34m.env\033[0m")
    print(" 3. Start development server: \033[34mnitrostack-py dev\033[0m (or `python -m nitrostack.cli.main dev`)")
    print(" 4. Preview widgets: \033[34mhttp://localhost:3000/widgets/preview\033[0m (server running)")
    print("    Static file (manual JSON): \033[34mopen widgets/preview.html\033[0m")
    print(" 5. Inspector (HTTP): \033[34mMCP_TRANSPORT_TYPE=http MCP_STATELESS=true NITROSTACK_APP_MODE=universal python main.py\033[0m")
    print("    Connect Streamable HTTP to \033[34mhttp://localhost:3000/mcp\033[0m with \033[34mAuthentication = Off\033[0m")
    print("    Apps tab renders the widget from live \033[34mstructuredContent\033[0m (not widgets/preview.html)")
    print("    For open shops only set \033[34mopenNow=true\033[0m (list) or \033[34mfilter=open_now\033[0m (map)")
    print(" 6. NitroStudio cannot folder-connect a Python project (it looks for @nitrostack/core).")
    print("    Use Inspector HTTP as above, or Studio's custom MCP URL if it offers Streamable HTTP.")
    print("\nHappy coding! 🚀\n")

def run_dev(port=None, widget=None):
    target = "main.py"
    if not os.path.exists(target):
        print("Error: main.py not found in current directory.")
        sys.exit(1)

    mcp_port, widgets_port = _resolve_runtime_ports(port, widget)

    print(f"Starting hot-reload development server for {target}...")
    print(f"MCP server port: {mcp_port}")
    process = None
    widgets_process = None

    # Check if Next.js widgets are present
    widgets_dir = os.path.join(os.getcwd(), "src", "widgets")
    has_widgets = os.path.exists(widgets_dir) and os.path.exists(os.path.join(widgets_dir, "package.json"))
    if has_widgets:
        print(f"Starting widget development server on port {widgets_port}...")
        try:
            widgets_process = _popen_npm(
                ["run", "dev", "--", "--port", str(widgets_port)],
                cwd=widgets_dir,
            )
        except Exception as e:
            print(f"Warning: Could not start widget development server: {e}")

    def start_process():
        nonlocal process
        if process:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.abspath(".")
        env["PORT"] = mcp_port
        if has_widgets:
            env["WIDGETS_DEV_PORT"] = str(widgets_port)
        process = subprocess.Popen([sys.executable, target], env=env)

    def cleanup():
        nonlocal process, widgets_process
        if process:
            try:
                process.terminate()
            except Exception:
                pass
        if widgets_process:
            try:
                widgets_process.terminate()
            except Exception:
                pass

    try:
        start_process()
        
        watched_extensions = {".py", ".env"}
        
        def get_mtimes():
            mtimes = {}
            for root, dirs, files in os.walk("."):
                if any(part.startswith(".") or part in ("venv", "env", "__pycache__") for part in root.split(os.sep)):
                    continue
                for file in files:
                    ext = os.path.splitext(file)[1]
                    if ext in watched_extensions:
                        path = os.path.join(root, file)
                        try:
                            mtimes[path] = os.path.getmtime(path)
                        except Exception:
                            pass
            return mtimes

        # Try using watchfiles for high-performance, low-CPU file monitoring
        try:
            from watchfiles import watch
            print("Using watchfiles for high-performance file monitoring.")
            
            while True:
                for changes in watch("."):
                    should_restart = False
                    for change_type, path in changes:
                        ext = os.path.splitext(path)[1]
                        if ext in watched_extensions:
                            parts = os.path.normpath(path).split(os.sep)
                            if not any(p in parts for p in ("venv", "env", "__pycache__", ".git", ".pytest_cache", "nitrostack.egg-info")):
                                should_restart = True
                                break
                    if should_restart:
                        print("File changes detected! Restarting server...")
                        start_process()
                
                time.sleep(0.5)
                if process and process.poll() is not None:
                    print("Server process exited. Waiting for file changes to restart...")
                    
        except ImportError:
            print("watchfiles library not found. Falling back to standard polling...")
            last_mtimes = get_mtimes()
            try:
                while True:
                    time.sleep(1)
                    if process and process.poll() is not None:
                        print("Server process exited. Waiting for file changes to restart...")
                    current_mtimes = get_mtimes()
                    changed = False
                    if set(current_mtimes.keys()) != set(last_mtimes.keys()):
                        changed = True
                    else:
                        for path, mtime in current_mtimes.items():
                            if last_mtimes.get(path) != mtime:
                                changed = True
                                break
                    if changed:
                        print("File changes detected! Restarting server...")
                        start_process()
                        last_mtimes = current_mtimes
            except KeyboardInterrupt:
                pass
    except KeyboardInterrupt:
        print("\nStopping development server...")
        cleanup()

def run_start(port=None, widget=None):
    target = "main.py"
    if not os.path.exists(target):
        print("Error: main.py not found in current directory.")
        sys.exit(1)

    mcp_port, widgets_port = _resolve_runtime_ports(port, widget)

    print(f"Starting production server for {target}...")
    print(f"MCP HTTP: http://localhost:{mcp_port}/mcp")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(".")
    env["PORT"] = mcp_port
    env["WIDGETS_DEV_PORT"] = widgets_port
    env["NODE_ENV"] = "production"
    # Select HTTP explicitly rather than letting NODE_ENV=production fall through
    # to dual. Dual shuts HTTP down when STDIO reaches EOF, so `start` would exit
    # immediately wherever stdin is not held open (Docker without -i, systemd, CI).
    # `setdefault` keeps an explicit MCP_TRANSPORT_TYPE from the caller.
    env.setdefault("MCP_TRANSPORT_TYPE", "http")

    widgets_dir = os.path.join(os.getcwd(), "src", "widgets")
    widgets_process = None
    if os.path.exists(widgets_dir) and os.path.exists(os.path.join(widgets_dir, "package.json")):
        # `next start` needs a production build; Popen would succeed and the child
        # would fail with a bare "Could not find a production build", so check here.
        if not os.path.exists(os.path.join(widgets_dir, ".next")):
            print("Widgets are not built yet — skipping the widget server.")
            print("Run 'npm run build' inside 'src/widgets', then retry.\n")
        else:
            print(f"Starting widget server on port {widgets_port}...")
            try:
                widgets_process = _popen_npm(
                    ["run", "start", "--", "--port", str(widgets_port)],
                    cwd=widgets_dir,
                )
            except Exception as e:
                print(f"Warning: Could not start widget server: {e}")

    try:
        subprocess.run([sys.executable, target], env=env)
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        if widgets_process:
            try:
                widgets_process.terminate()
            except Exception:
                pass

def generate_tool(name: str):
    # Both validators run before anything is written. `_validate_generate_name` and
    # `_sanitize_widget_route` accept overlapping-but-different character sets (e.g.
    # `_foo` is a valid identifier but not a valid route; `my-tool` is the reverse),
    # so validating the route lazily inside `write_widget_html` would leave an orphan
    # `{name}_tool.py` behind whenever the two disagree — and that orphan then blocks
    # any retry with "File already exists".
    try:
        name = _validate_generate_name(name)
        _sanitize_widget_route(name)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    filename = f"{name}_tool.py"
    dest_py = os.path.abspath(filename)
    try:
        _assert_dest_inside_root(dest_py, os.getcwd())
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    if os.path.exists(filename):
        print(f"Error: File '{filename}' already exists.")
        sys.exit(1)
    camel_name = "".join(part.capitalize() for part in name.split("_"))
    content = TOOL_TEMPLATE.format(name=name, camel_name=camel_name)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        html_path = write_widget_html(".", name, overwrite=False)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    write_widget_preview(".")
    print(f"Generated tool boilerplate in '{filename}'")
    print(f"Generated widget HTML in '{html_path}'")

def generate_module(name: str):
    generate_module_from_template(name)

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

def register_server(name: str, file_path: str):
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


def register_cursor_server(name: str, file_path: str = "main.py", *, global_config: bool = False, transport: str = "command"):
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

def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="nitrostack-py",
        description="nitrostack-py CLI — Scaffold, develop, pack, and run NitroStack Python MCP servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nitrostack-py init my-server\n"
            "  nitrostack-py generate guard MyGuard\n"
            "  nitrostack-py pack --dry-run\n"
            "  nitrostack-py upgrade --dry-run\n"
            "  nitrostack-py install --production\n"
            "  nitrostack-py validate\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new NitroStack MCP server project")
    init_parser.add_argument("name", nargs="?", default=None, help="Name of the project directory (prompted if omitted)")
    init_parser.add_argument(
        "--template",
        choices=list(OFFICIAL_TEMPLATES.keys()),
        default=None,
        help="Template to use: " + ", ".join(OFFICIAL_TEMPLATES) + " (default: interactive prompt)",
    )
    init_parser.add_argument("--skip-install", action="store_true", help="Skip installing Python (and widget npm) dependencies")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing agent skill directories when installing",
    )
    _add_port_flags(init_parser)

    # dev command
    dev_parser = subparsers.add_parser("dev", help="Start the hot-reloading development server")
    _add_port_flags(dev_parser)

    # start command
    start_parser = subparsers.add_parser("start", help="Start the production server")
    _add_port_flags(start_parser)

    # register command
    reg_parser = subparsers.add_parser("register", help="Register server script inside Claude Desktop configuration")
    reg_parser.add_argument("--name", default=os.path.basename(os.getcwd()), help="Name of the server (defaults to folder name)")
    reg_parser.add_argument("--file", default="main.py", help="Python script to register (defaults to main.py)")

    # cursor command
    cursor_parser = subparsers.add_parser("cursor", help="Register this server in Cursor MCP configuration")
    cursor_parser.add_argument("--global", dest="global_config", action="store_true", help="Write to ~/.cursor/mcp.json")
    cursor_parser.add_argument(
        "--transport",
        choices=("command", "streamable-http", "sse"),
        default="command",
        help="Connection type (default: command)",
    )
    cursor_parser.add_argument("--name", default=os.path.basename(os.getcwd()), help="Name of the server")
    cursor_parser.add_argument("--file", default="main.py", help="Python script to register for command transport")

    # generate command
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate boilerplate code (tool, module, guard, pipe, interceptor, filter, service)",
    )
    gen_subparsers = gen_parser.add_subparsers(dest="generator", metavar="type")

    tool_parser = gen_subparsers.add_parser("tool", help="Generate a new tool boilerplate")
    tool_parser.add_argument("name", help="Name of the tool")

    mod_parser = gen_subparsers.add_parser("module", help="Generate a new module boilerplate")
    mod_parser.add_argument("name", help="Name of the module")

    for kind, kind_help in (
        ("guard", "Generate an authorization guard"),
        ("pipe", "Generate a validation/transform pipe"),
        ("interceptor", "Generate an execution interceptor"),
        ("filter", "Generate an exception filter"),
        ("service", "Generate an injectable service"),
    ):
        kind_parser = gen_subparsers.add_parser(kind, help=kind_help)
        kind_parser.add_argument("name", help=f"Name of the {kind}")

    # pack command
    pack_parser = subparsers.add_parser(
        "pack",
        help="Build a deployable wheel of the current project (never includes .env/secrets)",
    )
    pack_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show files that would be packed without creating the artifact",
    )

    # upgrade command
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Update the nitrostack dependency to the latest (or a specific) PyPI version",
    )
    upgrade_parser.add_argument(
        "--version",
        dest="target_version",
        metavar="X.Y.Z",
        help="Pin nitrostack to this version instead of the latest PyPI release",
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the version change without modifying pyproject.toml",
    )
    upgrade_parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="Allow --version to pin an older release than the one currently declared",
    )

    # install command
    install_parser = subparsers.add_parser(
        "install",
        help="Install project dependencies from pyproject.toml / requirements.txt",
    )
    install_parser.add_argument(
        "--production",
        action="store_true",
        help="Skip development dependencies (optional extras and requirements-dev.txt)",
    )

    # validate command
    subparsers.add_parser(
        "validate",
        help="Lint project config, @mcp_app imports, and @module() class references",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    def _run_command(fn, *fn_args, **fn_kwargs):
        try:
            return fn(*fn_args, **fn_kwargs)
        except Exception as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    if args.command == "init":
        init_project(
            args.name,
            args.template,
            skip_install=args.skip_install,
            port=args.port,
            widget=args.widget,
            force=args.force,
        )
    elif args.command == "dev":
        run_dev(port=args.port, widget=args.widget)
    elif args.command == "start":
        run_start(port=args.port, widget=args.widget)
    elif args.command == "register":
        register_server(args.name, args.file)
    elif args.command == "cursor":
        register_cursor_server(args.name, args.file, global_config=args.global_config, transport=args.transport)
    elif args.command == "generate":
        if not args.generator:
            gen_parser.print_help()
            sys.exit(1)
        if args.generator == "tool":
            generate_tool(args.name)
        elif args.generator == "module":
            generate_module(args.name)
        else:
            generate_component(args.generator, args.name)
    elif args.command == "pack":
        _run_command(pack_project, dry_run=args.dry_run)
    elif args.command == "upgrade":
        _run_command(
            upgrade_project,
            version=args.target_version,
            dry_run=args.dry_run,
            allow_downgrade=args.allow_downgrade,
        )
    elif args.command == "install":
        _run_command(install_dependencies, production=args.production)
    elif args.command == "validate":
        issues = _run_command(validate_project)
        print(format_report(issues))
        if any(issue.severity == "error" for issue in issues):
            sys.exit(1)

if __name__ == "__main__":
    main()
