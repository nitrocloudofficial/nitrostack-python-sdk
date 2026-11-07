"""Project identity, dependency declarations, and generated packaging metadata."""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from nitrostack.cli._pack_files import _EXCLUDE_DIR_NAMES
from nitrostack.cli._shared import (
    parse_pyproject_dependencies, parse_requirements, read_text, requirement_name,
)


ENV_EXAMPLE_STUB = """PORT=8000
NODE_ENV=development
"""


_PYPROJECT_TEMPLATE = """[build-system]
requires = ["setuptools>=61.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "{version}"
description = "Packed NitroStack MCP server"
requires-python = ">=3.10"
dependencies = {deps}

[tool.setuptools]
py-modules = {py_modules}

[tool.setuptools.packages.find]
where = ["."]
namespaces = true
exclude = ["tests*", "venv*", "node_modules*", "src.widgets*"]

[tool.setuptools.package-data]
"*" = [".env.example", "requirements.txt", "*.md"]
"""


def _read_text(path: str) -> str:
    return read_text(path)


def _parse_pyproject_field(text: str, field: str) -> Optional[str]:
    match = re.search(rf'^{field}\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None


def _parse_pyproject_dependencies(text: str) -> List[str]:
    return parse_pyproject_dependencies(text)


def _parse_requirements(path: str) -> List[str]:
    return parse_requirements(path)


def _project_name_and_version(root: str) -> Tuple[str, str]:
    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject):
        text = _read_text(pyproject)
        name = _parse_pyproject_field(text, "name")
        version = _parse_pyproject_field(text, "version")
        if name and version:
            return name, version
        if name:
            return name, version or "0.1.0"
    return os.path.basename(os.path.abspath(root)) or "nitrostack-project", "0.1.0"


def _normalize_dist_name(name: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", name).strip("-").lower()
    return normalized or "nitrostack-project"


def _pep503_wheel_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name)


def _requirement_name(req: str) -> str:
    return requirement_name(req)


def requirements_from_project(root: str) -> List[str]:
    pyproject = os.path.join(root, "pyproject.toml")
    req_path = os.path.join(root, "requirements.txt")
    deps: List[str] = []
    name, _ = _project_name_and_version(root)
    if os.path.isfile(pyproject):
        deps = _parse_pyproject_dependencies(_read_text(pyproject))
    if not deps and os.path.isfile(req_path):
        deps = _parse_requirements(req_path)
    deps = [item.lstrip("\ufeff").strip() for item in deps if item.lstrip("\ufeff").strip()]
    has_nitrostack = any(_requirement_name(item) == "nitrostack" for item in deps)
    if not has_nitrostack and name.lower() != "nitrostack":
        deps = ["nitrostack", *deps]
    return deps


def write_requirements_txt(root: str, deps: Sequence[str]) -> str:
    path = os.path.join(root, "requirements.txt")
    body = "".join(f"{dep}\n" for dep in deps) if deps else "nitrostack\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def _ensure_env_example(root: str) -> str:
    path = os.path.join(root, ".env.example")
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(ENV_EXAMPLE_STUB)
    return path


def _discover_py_modules_and_packages(root: str) -> Tuple[List[str], List[str]]:
    py_modules: List[str] = []
    packages: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            for filename in filenames:
                if filename.endswith(".py") and filename != "setup.py":
                    py_modules.append(os.path.splitext(filename)[0])
            continue
        parts = rel_dir.replace("\\", "/").split("/")
        if any(part in _EXCLUDE_DIR_NAMES for part in parts):
            continue
        if any(filename.endswith(".py") for filename in filenames):
            packages.add(".".join(parts))
    return sorted(py_modules), sorted(packages)


def _toml_list(values: Iterable[str]) -> str:
    items = ", ".join(f'"{v}"' for v in values)
    return f"[{items}]"


def _ensure_pyproject(root: str, name: str, version: str, deps: Sequence[str]) -> None:
    path = os.path.join(root, "pyproject.toml")
    if os.path.isfile(path):
        return
    py_modules, _packages = _discover_py_modules_and_packages(root)
    content = _PYPROJECT_TEMPLATE.format(
        name=_normalize_dist_name(name),
        version=version,
        deps=_toml_list(deps),
        py_modules=_toml_list(py_modules),
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
