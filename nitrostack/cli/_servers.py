"""Development and production process lifecycles."""

import os
import sys
import subprocess
import time


def run_dev(port=None, widget=None, *, _popen_npm, _resolve_runtime_ports):
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

def run_start(port=None, widget=None, *, _popen_npm, _resolve_runtime_ports):
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
