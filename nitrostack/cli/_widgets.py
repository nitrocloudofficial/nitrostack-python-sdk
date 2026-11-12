"""Widget scaffolding and safe output paths."""

import os
import re
from pathlib import Path


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

def write_widget_html(
    project_dir: str,
    route: str,
    *,
    overwrite: bool = False,
    _assert_dest_inside_root,
    _sanitize_widget_route,
) -> str:
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

def write_widget_preview(
    project_dir: str,
    *,
    SAMPLE_PIZZA_PREVIEW_JSON,
    WIDGET_PREVIEW_HTML,
) -> None:
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

def ensure_python_widgets(
    project_dir: str,
    *,
    Path,
    write_widget_html,
    write_widget_preview,
) -> list:
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
