"""Dependency installation wrapper for `nitrostack-py install`."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import List, Optional


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


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
    print(f"Target: {venv_dir(root)}")

    if os.path.isfile(pyproject):
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
