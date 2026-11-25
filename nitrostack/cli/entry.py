"""Fast CLI entry point: argparse and --help without heavy SDK imports."""

from __future__ import annotations

import sys

from nitrostack.cli._config import DEFAULT_MCP_PORT, DEFAULT_WIDGETS_PORT, OFFICIAL_TEMPLATES
from nitrostack.cli._parser import build_parser
from nitrostack.cli import _runtime


def _add_port_flags(parser):
    return _runtime._add_port_flags(
        parser,
        DEFAULT_MCP_PORT=DEFAULT_MCP_PORT,
        DEFAULT_WIDGETS_PORT=DEFAULT_WIDGETS_PORT,
    )


def main() -> None:
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
        _add_port_flags=_add_port_flags,
        OFFICIAL_TEMPLATES=OFFICIAL_TEMPLATES,
    )
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    from nitrostack.cli.main import dispatch_command
    from nitrostack.cli.telemetry import (
        maybe_print_first_run_notice,
        shutdown_telemetry,
        track_command_invoked,
    )

    try:
        maybe_print_first_run_notice()
        track_command_invoked(args.command)
        dispatch_command(args, gen_parser)
    finally:
        shutdown_telemetry()