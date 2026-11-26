"""Development and production process lifecycles."""

import os
import signal
import sys
import subprocess
import time

_DEV_SKIP_DIR_PARTS = frozenset(
    {
        "venv",
        "env",
        "__pycache__",
        ".git",
        ".pytest_cache",
        "nitrostack.egg-info",
        "vendor",
        "node_modules",
    }
)
_CHILD_GRACE_SECONDS = 12


def _path_parts(path) -> list[str]:
    return [part for part in os.path.normpath(os.fspath(path)).split(os.sep) if part]


def _watched_project_path(path, watched_extensions: set[str]) -> bool:
    fspath = os.fspath(path)
    ext = os.path.splitext(fspath)[1]
    if ext not in watched_extensions:
        return False
    parts = _path_parts(path)
    basename = parts[-1] if parts else ""
    if basename == ".env":
        return True
    return not any(
        part in _DEV_SKIP_DIR_PARTS or (part.startswith(".") and part not in {".", ".env"})
        for part in parts
    )


def _skip_polling_root(root: str) -> bool:
    rel = os.path.normpath(root)
    if rel in (".", ""):
        return False
    parts = [part for part in rel.split(os.sep) if part and part != "."]
    return any(part.startswith(".") or part in _DEV_SKIP_DIR_PARTS for part in parts)


def _own_group(process: subprocess.Popen) -> int | None:
    """The child's process group, only when it leads one we created.

    Never return our own group (or init's): signalling it would kill this CLI,
    and on CI the runner itself.
    """
    if sys.platform == "win32":
        return None
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 1:
        return None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    if pgid != pid or pgid == os.getpgrp():
        return None
    return pgid


def _signal_child(process: subprocess.Popen, *, force: bool) -> None:
    pgid = _own_group(process)
    if pgid is not None:
        os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
    elif force:
        process.kill()
    else:
        process.terminate()


def _stop_child(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        _signal_child(process, force=False)
    except (ProcessLookupError, OSError, AttributeError):
        try:
            process.terminate()
        except Exception:
            pass
    try:
        process.wait(timeout=_CHILD_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        _signal_child(process, force=True)
    except (ProcessLookupError, OSError, AttributeError):
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=3)
    except Exception:
        pass


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
        _stop_child(process)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.abspath(".")
        env["PORT"] = mcp_port
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if has_widgets:
            env["WIDGETS_DEV_PORT"] = str(widgets_port)
        # Inherit stdout/stderr so server logs and tracebacks stay visible.
        popen_kwargs = {"env": env, "cwd": os.getcwd()}
        if sys.platform == "win32":
            process = subprocess.Popen([sys.executable, target], **popen_kwargs)
        else:
            process = subprocess.Popen(
                [sys.executable, target],
                start_new_session=True,
                **popen_kwargs,
            )

    def cleanup():
        nonlocal process, widgets_process
        _stop_child(process)
        process = None
        if widgets_process:
            try:
                widgets_process.terminate()
                widgets_process.wait(timeout=5)
            except Exception:
                try:
                    widgets_process.kill()
                except Exception:
                    pass

    try:
        start_process()

        watched_extensions = {".py", ".env"}

        def get_mtimes():
            mtimes = {}
            for root, _, files in os.walk("."):
                if _skip_polling_root(root):
                    continue
                for file in files:
                    ext = os.path.splitext(file)[1]
                    if ext in watched_extensions:
                        path = os.path.join(root, file)
                        if _watched_project_path(path, watched_extensions):
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
                    for _change_type, path in changes:
                        if _watched_project_path(path, watched_extensions):
                            should_restart = True
                            break
                    if should_restart:
                        print("File changes detected! Restarting server...")
                        start_process()
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