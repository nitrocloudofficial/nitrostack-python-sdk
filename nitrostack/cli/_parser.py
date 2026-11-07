"""CLI argument declarations; dispatch stays in the public entry module."""

import argparse
import os


def build_parser(*, _add_port_flags, OFFICIAL_TEMPLATES):
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

    return parser, gen_parser
