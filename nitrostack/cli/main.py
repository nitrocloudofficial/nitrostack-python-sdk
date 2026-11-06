"""CLI entry points and backwards-compatible helper imports."""

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
from nitrostack.cli._parser import build_parser
from nitrostack.cli import _widgets
from nitrostack.cli import _interaction
from nitrostack.cli import _runtime
from nitrostack.cli import _project_init
from nitrostack.cli import _servers
from nitrostack.cli import _scaffolding
from nitrostack.cli import _registration


_GENERATE_NAME_RE = _widgets._GENERATE_NAME_RE
_WIDGET_ROUTE_RE = _widgets._WIDGET_ROUTE_RE


_validate_generate_name = _widgets._validate_generate_name


_sanitize_widget_route = _widgets._sanitize_widget_route


_assert_dest_inside_root = _widgets._assert_dest_inside_root


def write_widget_html(project_dir: str, route: str, *, overwrite: bool = False) -> str:
    'Write ``widgets/out/{route}.html`` if missing (Python-only static widget).'
    return _widgets.write_widget_html(
        project_dir,
        route,
        overwrite=overwrite,
        _assert_dest_inside_root=_assert_dest_inside_root,
        _sanitize_widget_route=_sanitize_widget_route,
    )


def write_widget_preview(project_dir: str) -> None:
    return _widgets.write_widget_preview(
        project_dir,
        SAMPLE_PIZZA_PREVIEW_JSON=SAMPLE_PIZZA_PREVIEW_JSON,
        WIDGET_PREVIEW_HTML=WIDGET_PREVIEW_HTML,
    )


def ensure_python_widgets(project_dir: str) -> list:
    'Create missing ``widgets/out/{route}.html`` for every ``@widget`` in the project.'
    return _widgets.ensure_python_widgets(
        project_dir,
        Path=Path,
        write_widget_html=write_widget_html,
        write_widget_preview=write_widget_preview,
    )


print_banner = _interaction.print_banner


_stdin_is_interactive = _interaction._stdin_is_interactive


def _prompt(message: str, default: str = "") -> str:
    return _interaction._prompt(message, default, _stdin_is_interactive=_stdin_is_interactive)


def _resolve_template(template: str) -> str:
    return _interaction._resolve_template(template, OFFICIAL_TEMPLATES=OFFICIAL_TEMPLATES)


def _prompt_template() -> str:
    return _interaction._prompt_template(OFFICIAL_TEMPLATES=OFFICIAL_TEMPLATES, _prompt=_prompt)


def _prompt_yes_no(message: str, default_yes: bool = True) -> bool:
    return _interaction._prompt_yes_no(message, default_yes, _stdin_is_interactive=_stdin_is_interactive)


_normalize_port = _runtime._normalize_port


_read_project_env = _runtime._read_project_env


def _resolve_runtime_ports(port=None, widget=None):
    'Resolve MCP and widget ports. Explicit --port/--widget flags override defaults.'
    return _runtime._resolve_runtime_ports(
        port,
        widget,
        DEFAULT_MCP_PORT=DEFAULT_MCP_PORT,
        DEFAULT_WIDGETS_PORT=DEFAULT_WIDGETS_PORT,
        _normalize_port=_normalize_port,
        _read_project_env=_read_project_env,
    )


_upsert_env_var = _runtime._upsert_env_var


_find_npm = _runtime._find_npm


def _run_npm(args, **kwargs):
    'Run npm without shell=True. A list + shell=True is treated as `sh -c npm` and drops flags.'
    return _runtime._run_npm(args, _find_npm=_find_npm, **kwargs)


def _popen_npm(args, **kwargs):
    return _runtime._popen_npm(args, _find_npm=_find_npm, **kwargs)


def _add_port_flags(parser):
    return _runtime._add_port_flags(
        parser,
        DEFAULT_MCP_PORT=DEFAULT_MCP_PORT,
        DEFAULT_WIDGETS_PORT=DEFAULT_WIDGETS_PORT,
    )


def init_project(name: str = None, template: str = None, skip_install: bool = False, port=None, widget=None, force: bool = False):
    return _project_init.init_project(
        name,
        template,
        skip_install,
        port,
        widget,
        force,
        DEFAULT_MCP_PORT=DEFAULT_MCP_PORT,
        DEFAULT_PROJECT_NAME=DEFAULT_PROJECT_NAME,
        DEFAULT_WIDGETS_PORT=DEFAULT_WIDGETS_PORT,
        OFFICIAL_TEMPLATES=OFFICIAL_TEMPLATES,
        _normalize_port=_normalize_port,
        _prompt=_prompt,
        _prompt_template=_prompt_template,
        _prompt_yes_no=_prompt_yes_no,
        _resolve_template=_resolve_template,
        _run_npm=_run_npm,
        _stdin_is_interactive=_stdin_is_interactive,
        _upsert_env_var=_upsert_env_var,
        ensure_python_widgets=ensure_python_widgets,
        install_dependencies=install_dependencies,
        lock_with_uv=lock_with_uv,
        print_banner=print_banner,
        rewrite_pyproject_identity=rewrite_pyproject_identity,
        run_skills_flow=run_skills_flow,
    )


def run_dev(port=None, widget=None):
    return _servers.run_dev(
        port,
        widget,
        _popen_npm=_popen_npm,
        _resolve_runtime_ports=_resolve_runtime_ports,
    )


def run_start(port=None, widget=None):
    return _servers.run_start(
        port,
        widget,
        _popen_npm=_popen_npm,
        _resolve_runtime_ports=_resolve_runtime_ports,
    )


def generate_tool(name: str):
    return _scaffolding.generate_tool(
        name,
        TOOL_TEMPLATE=TOOL_TEMPLATE,
        _assert_dest_inside_root=_assert_dest_inside_root,
        _sanitize_widget_route=_sanitize_widget_route,
        _validate_generate_name=_validate_generate_name,
        write_widget_html=write_widget_html,
        write_widget_preview=write_widget_preview,
    )


def generate_module(name: str):
    return _scaffolding.generate_module(name, generate_module_from_template=generate_module_from_template)


get_claude_config_paths = _registration.get_claude_config_paths


def register_server(name: str, file_path: str):
    return _registration.register_server(
        name,
        file_path,
        _build_server_entry=_build_server_entry,
        get_claude_config_paths=get_claude_config_paths,
    )


_build_server_entry = _registration._build_server_entry


_write_json_atomic = _registration._write_json_atomic


def register_cursor_server(name: str, file_path: str = "main.py", *, global_config: bool = False, transport: str = "command"):
    "Merge this project into Cursor's project or user MCP configuration."
    return _registration.register_cursor_server(
        name,
        file_path,
        global_config=global_config,
        transport=transport,
        _build_server_entry=_build_server_entry,
        _write_json_atomic=_write_json_atomic,
    )


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

    parser, gen_parser = build_parser(
        _add_port_flags=_add_port_flags, OFFICIAL_TEMPLATES=OFFICIAL_TEMPLATES,
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
