"""Tool and module generation commands."""

import os
import sys


def generate_tool(
    name: str,
    *,
    TOOL_TEMPLATE,
    _assert_dest_inside_root,
    _sanitize_widget_route,
    _validate_generate_name,
    write_widget_html,
    write_widget_preview,
):
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

def generate_module(name: str, *, generate_module_from_template):
    generate_module_from_template(name)
