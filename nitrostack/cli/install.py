"""Dependency installation wrapper for `nitrostack-py install`."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import List, Optional

from nitrostack.cli._shared import write_text_atomic

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_PEP503_KEEP = re.compile(r"[^A-Za-z0-9._-]+")
_PEP503_DASH = re.compile(r"[-_.]+")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def pep503_name(raw: str) -> str:
    """Normalize a folder or display name to a PEP 503 distribution name."""
    value = _PEP503_KEEP.sub("-", (raw or "").strip())
    value = value.strip("-._").lower()
    value = _PEP503_DASH.sub("-", value)
    return value or "nitrostack-app"


def rewrite_pyproject_identity(
    path: str,
    *,
    name: str,
    description: Optional[str] = None,
) -> None:
    """Set ``[project] name`` (and optional description) in a template pyproject."""
    if not os.path.isfile(path):
        return
    text = _read(path)
    dist = pep503_name(name)
    text, _ = re.subn(r'(?m)^(name\s*=\s*")[^"]*(")', rf"\g<1>{dist}\2", text, count=1)
    if description is not None:
        escaped = description.replace("\\", "\\\\").replace('"', '\\"')
        text, _ = re.subn(
            r'(?m)^(description\s*=\s*")[^"]*(")',
            rf"\g<1>{escaped}\2",
            text,
            count=1,
        )
    write_text_atomic(path, text)


def requirements_uses_local_nitrostack(path: str) -> bool:
    """True when requirements.txt pins nitrostack (or the SDK) as an editable/path."""
    if not os.path.isfile(path):
        return False
    for raw in _read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith("-e ") or lower.startswith("--editable"):
            return True
        if lower.startswith("file:") or " @ file:" in lower:
            return True
        if lower.startswith("./") or lower.startswith("../") or os.path.isabs(line.split()[0]):
            return True
        if "nitrostack" in lower and ("/" in line or "\\" in line):
            return True
    return False


def _uv_bin() -> Optional[str]:
    return shutil.which("uv")


def _optional_extra_names(pyproject_text: str) -> List[str]:
    """Return optional-dependency extra names (e.g. dev, test)."""
    match = re.search(
        r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)",
        pyproject_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    names = re.findall(r"^([A-Za-z0-9._-]+)\s*=", match.group(1), re.MULTILINE)
    return names


def venv_dir(root: str) -> str:
    return os.path.join(root, ".venv")


def venv_python(root: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir(root), "Scripts", "python.exe")
    return os.path.join(venv_dir(root), "bin", "python")


def ensure_project_venv(root: str) -> str:
    """Create ``<root>/.venv`` if needed and return its Python executable."""
    python = venv_python(root)
    if os.path.isfile(python):
        return python
    dest = venv_dir(root)
    print(f"Creating virtualenv: {dest}")
    result = subprocess.run([sys.executable, "-m", "venv", dest])
    if result.returncode != 0 or not os.path.isfile(python):
        raise RuntimeError(
            f"Failed to create {dest}.\n"
            f"Create it manually with `{sys.executable} -m venv .venv`, then retry."
        )
    return python


def _run_pip(args: List[str], cwd: str) -> None:
    python = ensure_project_venv(cwd)
    cmd = [python, "-m", "pip", "install", *args]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"`pip install` failed with exit code {result.returncode}.\n"
            "Fix the reported dependency error and retry `nitrostack-py install`."
        )


def _run_uv(args: List[str], cwd: str) -> None:
    uv = _uv_bin()
    if not uv:
        raise RuntimeError("uv is not installed")
    cmd = [uv, *args]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"`uv {' '.join(args)}` failed with exit code {result.returncode}.\n"
            "Fix the reported dependency error and retry `nitrostack-py install`."
        )


def _skip_uv_lock() -> bool:
    return os.environ.get("NITROSTACK_SKIP_UV_LOCK", "").strip().lower() in _TRUE_TOKENS


def lock_with_uv(cwd: str) -> bool:
    """Run ``uv lock`` when uv is available. Missing uv is a skip, not a failure."""
    if _skip_uv_lock():
        return False
    root = os.path.abspath(cwd)
    if not os.path.isfile(os.path.join(root, "pyproject.toml")):
        return False
    if not _uv_bin():
        print("uv not found; skip lock. Run `uv lock` in this directory.")
        return False
    try:
        _run_uv(["lock"], cwd=root)
    except RuntimeError as exc:
        print(f"Warning: {exc}")
        print("Run `uv lock` in the project directory.")
        return False
    print("Wrote uv.lock")
    return True


def install_dependencies(
    *,
    production: bool = False,
    cwd: Optional[str] = None,
) -> None:
    root = os.path.abspath(cwd or os.getcwd())
    pyproject = os.path.join(root, "pyproject.toml")
    requirements = os.path.join(root, "requirements.txt")
    dev_requirement_files = [
        os.path.join(root, "requirements-dev.txt"),
        os.path.join(root, "requirements.dev.txt"),
        os.path.join(root, "dev-requirements.txt"),
    ]

    if not os.path.isfile(pyproject) and not os.path.isfile(requirements):
        raise RuntimeError(
            "No pyproject.toml or requirements.txt found in the current directory.\n"
            "Run this command from a NitroStack project."
        )

    print("NITROSTACK — Install" + (" (production)" if production else ""))
    local_pin = requirements_uses_local_nitrostack(requirements)
    use_uv = bool(_uv_bin() and os.path.isfile(pyproject) and not local_pin)
    print(f"Target: {venv_dir(root)}")
    if use_uv:
        args = ["sync"]
        if production:
            args.append("--no-dev")
        _run_uv(args, cwd=root)
        print("Installed with uv sync"
              + (" (skipped optional/dev extras)" if production else ""))
        if production:
            print("Skipping development dependency files (--production).")
        return

    if local_pin and os.path.isfile(requirements):
        _run_pip(["-r", requirements], cwd=root)
        print("Installed requirements.txt")
    elif os.path.isfile(pyproject):
        extras: List[str] = []
        if not production:
            extras = _optional_extra_names(_read(pyproject))
        if extras:
            extra_spec = ",".join(extras)
            _run_pip(["-e", f".[{extra_spec}]"], cwd=root)
        else:
            _run_pip(["-e", "."], cwd=root)
        print("Installed pyproject.toml dependencies"
              + (" (skipped optional/dev extras)" if production else ""))
    elif os.path.isfile(requirements):
        _run_pip(["-r", requirements], cwd=root)
        print("Installed requirements.txt")

    if production:
        print("Skipping development dependency files (--production).")
        return

    for path in dev_requirement_files:
        if os.path.isfile(path):
            _run_pip(["-r", path], cwd=root)
            print(f"Installed {os.path.basename(path)}")
