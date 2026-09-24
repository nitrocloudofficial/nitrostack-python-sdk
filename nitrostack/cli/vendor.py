"""Vendor the running NitroStack SDK into a scaffolded project.

Cloud deploy zips the project (not the developer's .venv) and `uv sync`s
from `pyproject.toml`. Putting a PEP 508 `file:` URL in `[project].dependencies`
breaks NitroCloud's Python uv builder (`Failed to parse metadata from built
wheel`). Keep the dependency name as `nitrostack` and point uv at the copy
with `[tool.uv.sources]`.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from nitrostack.cli._shared import write_text_atomic

VENDOR_REL = os.path.join("vendor", "nitrostack")
_REQ_PATH = "./vendor/nitrostack"

_SKIP_DIR_NAMES = {
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "templates",
    ".egg-info",
}

_QUOTED_NITROSTACK = re.compile(
    r'(["\'])nitrostack(?:\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[^"\']+)?'
    r'(?:\s*@\s*file:[^"\']+)?\1',
    re.IGNORECASE,
)


def running_nitrostack_root() -> Path:
    """Directory that contains the `nitrostack` package used by this CLI."""
    import nitrostack

    package_dir = Path(nitrostack.__file__).resolve().parent
    parent = package_dir.parent
    pyproject = parent / "pyproject.toml"
    if pyproject.is_file() and "name" in pyproject.read_text(encoding="utf-8"):
        return parent
    return package_dir


def _ignore(_directory: str, names: list) -> list:
    skipped = []
    for name in names:
        if name in _SKIP_DIR_NAMES or name.endswith(".egg-info"):
            skipped.append(name)
    return skipped


def _write_minimal_pyproject(dest: Path) -> None:
    import nitrostack

    version = getattr(nitrostack, "__version__", None) or "0.3.2"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=61.0.0"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "nitrostack"\n'
        f'version = "{version}"\n'
        'description = "NitroStack Python SDK (vendored)"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = [\n"
        '    "mcp>=2,<3",\n'
        '    "pydantic>=2.0.0",\n'
        '    "starlette>=0.30.0",\n'
        '    "uvicorn>=0.20.0",\n'
        '    "watchfiles>=0.18.0",\n'
        '    "anyio>=4.0.0",\n'
        '    "packaging>=23.0.0",\n'
        '    "tomli>=2.0.0; python_version < \'3.11\'",\n'
        "]\n\n"
        "[project.scripts]\n"
        'nitrostack-py = "nitrostack.cli.main:main"\n\n'
        "[tool.setuptools.packages.find]\n"
        'include = ["nitrostack*"]\n\n'
        "[tool.setuptools]\n"
        "include-package-data = true\n\n"
        "[tool.setuptools.package-data]\n"
        'nitrostack = ["transports/assets/*"]\n',
        encoding="utf-8",
    )


def copy_running_nitrostack(dest: Path) -> None:
    """Copy the CLI's nitrostack package into ``dest`` (a installable tree)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    root = running_nitrostack_root()
    src_package = root / "nitrostack"
    if not src_package.is_dir():
        src_package = root
    shutil.copytree(src_package, dest / "nitrostack", ignore=_ignore, dirs_exist_ok=True)

    src_pyproject = root / "pyproject.toml"
    if src_pyproject.is_file() and src_package != root:
        shutil.copy2(src_pyproject, dest / "pyproject.toml")
        manifest = root / "MANIFEST.in"
        if manifest.is_file():
            shutil.copy2(manifest, dest / "MANIFEST.in")
    else:
        _write_minimal_pyproject(dest)

    assets = dest / "nitrostack" / "transports" / "assets" / "landing.html"
    if not assets.is_file():
        raise RuntimeError(
            f"Vendored SDK is missing the documentation page at {assets}. "
            "Update nitrostack and retry."
        )


def _pin_text(text: str) -> str:
    """Keep `[project].dependencies` as a plain name (valid wheel Requires-Dist)."""
    return _QUOTED_NITROSTACK.sub(lambda match: f"{match.group(1)}nitrostack{match.group(1)}", text)


def _pin_requirements(path: Path) -> None:
    if not path.is_file():
        path.write_text(f"{_REQ_PATH}\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and re.match(r"(?:-e\s+)?(?:\./)?nitrostack(?![\w.-])", stripped, re.I):
            out.append(f"{_REQ_PATH}\n")
            found = True
        elif stripped in {"./vendor/nitrostack", "-e ./vendor/nitrostack"}:
            out.append(f"{_REQ_PATH}\n")
            found = True
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{_REQ_PATH}\n")
    path.write_text("".join(out), encoding="utf-8")


def _ensure_uv_source(text: str) -> str:
    if re.search(r"^\[tool\.uv\.sources\]", text, re.MULTILINE):
        if re.search(r"(?m)^nitrostack\s*=", text):
            return re.sub(
                r"(?m)^nitrostack\s*=\s*.*$",
                'nitrostack = { path = "vendor/nitrostack" }',
                text,
                count=1,
            )
        return re.sub(
            r"(?m)^(\[tool\.uv\.sources\]\s*)",
            r'\1nitrostack = { path = "vendor/nitrostack" }\n',
            text,
            count=1,
        )
    if not text.endswith("\n"):
        text += "\n"
    return text + '\n[tool.uv.sources]\nnitrostack = { path = "vendor/nitrostack" }\n'


def pin_project_to_vendor(project: Path) -> None:
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        write_text_atomic(str(pyproject), _ensure_uv_source(_pin_text(text)))
    _pin_requirements(project / "requirements.txt")


def ensure_vendored_nitrostack(project: str, *, quiet: bool = False) -> Optional[str]:
    """Copy this CLI's SDK into ``project/vendor/nitrostack`` and pin deps.

    Returns the vendor path, or None when the directory is not a project.
    """
    root = Path(os.path.abspath(project))
    if not (root / "pyproject.toml").is_file() and not (root / "requirements.txt").is_file():
        return None
    if (root / "nitrostack" / "transports" / "assets" / "landing.html").is_file():
        return None
    dest = root / VENDOR_REL
    copy_running_nitrostack(dest)
    pin_project_to_vendor(root)
    if not quiet:
        print(f"Vendored NitroStack SDK at {dest}")
    return str(dest)
