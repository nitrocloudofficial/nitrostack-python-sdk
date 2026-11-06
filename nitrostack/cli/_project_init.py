"""Project initialization and dependency setup."""

import os
import sys
import shutil
import subprocess


def init_project(
    name: str = None,
    template: str = None,
    skip_install: bool = False,
    port = None,
    widget = None,
    force: bool = False,
    *,
    DEFAULT_MCP_PORT,
    DEFAULT_PROJECT_NAME,
    DEFAULT_WIDGETS_PORT,
    OFFICIAL_TEMPLATES,
    _normalize_port,
    _prompt,
    _prompt_template,
    _prompt_yes_no,
    _resolve_template,
    _run_npm,
    _stdin_is_interactive,
    _upsert_env_var,
    ensure_python_widgets,
    install_dependencies,
    lock_with_uv,
    print_banner,
    rewrite_pyproject_identity,
    run_skills_flow,
):
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
