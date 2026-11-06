"""Terminal prompts and template selection."""

import sys


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

def _prompt(message: str, default: str='', *, _stdin_is_interactive) -> str:
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

def _resolve_template(template: str, *, OFFICIAL_TEMPLATES) -> str:
    key = (template or "").strip().lower()
    if key not in OFFICIAL_TEMPLATES:
        names = ", ".join(OFFICIAL_TEMPLATES)
        print(f"Error: Unknown template '{template}'. Use one of: {names}")
        sys.exit(1)
    return key

def _prompt_template(*, OFFICIAL_TEMPLATES, _prompt) -> str:
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

def _prompt_yes_no(message: str, default_yes: bool=True, *, _stdin_is_interactive) -> bool:
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
