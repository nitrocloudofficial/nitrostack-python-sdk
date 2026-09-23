"""Project packing for `nitrostack-py pack`.

Builds a deployable wheel of the current project using the setuptools backend
already declared in NitroStack's packaging config. Secrets (``.env``) are never
included. Temporary build artifacts are cleaned up after packing.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import zipfile
from fnmatch import fnmatch
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from nitrostack.cli._shared import (
    EXCLUDE_DIR_NAMES,
    parse_pyproject_dependencies,
    parse_requirements,
    read_text,
    requirement_name,
)

_EXCLUDE_DIR_NAMES = EXCLUDE_DIR_NAMES

_EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".egg",
}

_EXCLUDE_NAME_GLOBS = {
    "*.egg-info",
}

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


def _is_secret_env(name: str) -> bool:
    if name == ".env.example":
        return False
    return name == ".env" or name.startswith(".env.")


def _load_gitignore_patterns(root: str) -> List[Tuple[str, bool]]:
    """Return (pattern, is_negation) pairs from .gitignore if present."""
    path = os.path.join(root, ".gitignore")
    patterns: List[Tuple[str, bool]] = []
    if not os.path.isfile(path):
        return patterns
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                negated = line.startswith("!")
                if negated:
                    line = line[1:]
                patterns.append((line.rstrip("/"), negated))
    except OSError:
        return []
    return patterns


def _matches_gitignore(rel_posix: str, patterns: Sequence[Tuple[str, bool]]) -> bool:
    ignored = False
    basename = rel_posix.rsplit("/", 1)[-1]
    for pattern, negated in patterns:
        pat = pattern[2:] if pattern.startswith("./") else pattern
        hit = (
            fnmatch(rel_posix, pat)
            or fnmatch(rel_posix, pat.rstrip("/") + "/*")
            or fnmatch(basename, pat)
            or (pat.endswith("/") and (rel_posix.startswith(pat) or rel_posix.startswith(pat.rstrip("/"))))
        )
        if hit:
            ignored = not negated
    return ignored


def _should_exclude(rel_posix: str, name: str, is_dir: bool, gitignore: Sequence[Tuple[str, bool]]) -> bool:
    if name == ".env.example":
        return False
    if _is_secret_env(name):
        return True
    if name in _EXCLUDE_DIR_NAMES and is_dir:
        return True
    if any(fnmatch(name, glob) for glob in _EXCLUDE_NAME_GLOBS):
        return True
    if any(name.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES):
        return True
    if name.endswith(".egg-info") or ".egg-info/" in rel_posix:
        return True
    if _matches_gitignore(rel_posix, gitignore):
        return True
    return False


def collect_pack_files(root: str) -> List[str]:
    """Return posix-relative paths that would be included in a pack."""
    gitignore = _load_gitignore_patterns(root)
    included: List[str] = []
    root = os.path.abspath(root)

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir_posix = "" if rel_dir == "." else rel_dir.replace("\\", "/")

        kept_dirs = []
        for dirname in dirnames:
            child_rel = f"{rel_dir_posix}/{dirname}" if rel_dir_posix else dirname
            if _should_exclude(child_rel, dirname, True, gitignore):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            child_rel = f"{rel_dir_posix}/{filename}" if rel_dir_posix else filename
            if _should_exclude(child_rel, filename, False, gitignore):
                continue
            included.append(child_rel)

    if ".env.example" not in included:
        included.append(".env.example")
    included.sort()
    return included


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


def _rmtree(path: str) -> None:
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def _copy_selected(src_root: str, dest_root: str, files: Sequence[str]) -> None:
    for rel in files:
        if rel == ".env.example":
            continue  # handled separately so a stub can be created
        src = os.path.join(src_root, rel.replace("/", os.sep))
        dest = os.path.join(dest_root, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(dest) or dest_root, exist_ok=True)
        shutil.copy2(src, dest)


def _sha256_record(data: bytes) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _inject_into_wheel(wheel_path: str, files: Sequence[Tuple[str, str]]) -> None:
    """Add extra files to a built wheel and rewrite RECORD so the wheel stays valid."""
    with zipfile.ZipFile(wheel_path, "r") as src:
        contents = {name: src.read(name) for name in src.namelist()}

    for arcname, source in files:
        posix = arcname.replace("\\", "/")
        if posix in contents or not os.path.isfile(source):
            continue
        with open(source, "rb") as handle:
            contents[posix] = handle.read()

    record_name = next((n for n in contents if n.endswith(".dist-info/RECORD")), None)
    if record_name:
        rows = []
        for name in sorted(contents):
            if name == record_name:
                continue
            data = contents[name]
            rows.append(f"{name},{_sha256_record(data)},{len(data)}")
        rows.append(f"{record_name},,")
        contents[record_name] = ("\n".join(rows) + "\n").encode("utf-8")

    tmp_path = wheel_path + ".tmp"
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, data in contents.items():
            dest.writestr(name, data)
    os.replace(tmp_path, wheel_path)


def _is_valid_wheel(path: str) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        has_wheel = any(n.endswith(".dist-info/WHEEL") for n in names)
        has_meta = any(n.endswith(".dist-info/METADATA") for n in names)
        has_record = any(n.endswith(".dist-info/RECORD") for n in names)
        if not (has_wheel and has_meta and has_record):
            return False
        wheel_name = next(n for n in names if n.endswith(".dist-info/WHEEL"))
        body = zf.read(wheel_name).decode("utf-8", errors="replace")
        return "Wheel-Version:" in body


def _collect_staging_bytes(src_root: str) -> dict:
    contents = {}
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")]
        for filename in filenames:
            if any(filename.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES):
                continue
            if _is_secret_env(filename):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, src_root).replace("\\", "/")
            with open(full, "rb") as handle:
                contents[rel] = handle.read()
    return contents


def _write_purelib_wheel(
    src_root: str,
    wheel_dir: str,
    name: str,
    version: str,
    deps: Sequence[str],
) -> str:
    """Write a PEP 427 py3-none-any wheel from the staged project files."""
    os.makedirs(wheel_dir, exist_ok=True)
    dist_name = _pep503_wheel_name(_normalize_dist_name(name))
    display_name = _normalize_dist_name(name)
    wheel_filename = f"{dist_name}-{version}-py3-none-any.whl"
    wheel_path = os.path.join(wheel_dir, wheel_filename)
    dist_info = f"{dist_name}-{version}.dist-info"

    contents = _collect_staging_bytes(src_root)
    metadata_lines = [
        "Metadata-Version: 2.1",
        f"Name: {display_name}",
        f"Version: {version}",
        "Summary: Packed NitroStack MCP server",
        "Requires-Python: >=3.10",
    ]
    for dep in deps:
        metadata_lines.append(f"Requires-Dist: {dep}")
    contents[f"{dist_info}/METADATA"] = ("\n".join(metadata_lines) + "\n").encode("utf-8")
    contents[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: nitrostack-py pack\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode("utf-8")

    record_name = f"{dist_info}/RECORD"
    rows = []
    for arcname in sorted(contents):
        data = contents[arcname]
        rows.append(f"{arcname},{_sha256_record(data)},{len(data)}")
    rows.append(f"{record_name},,")
    contents[record_name] = ("\n".join(rows) + "\n").encode("utf-8")

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in contents.items():
            zf.writestr(arcname, data)
    return wheel_path


def _setuptools_build_wheel(wheel_dir: str) -> str:
    from setuptools.build_meta import build_wheel as setuptools_build_wheel
    return setuptools_build_wheel(wheel_dir)


def _build_wheel_with_setuptools(src_root: str, wheel_dir: str) -> Optional[str]:
    """Use the setuptools PEP 517 backend when it is importable."""
    os.makedirs(wheel_dir, exist_ok=True)
    previous = os.getcwd()
    try:
        os.chdir(src_root)
        filename = _setuptools_build_wheel(wheel_dir)
    except ImportError as exc:
        print(f"Warning: setuptools build backend unavailable, falling back: {exc}")
        return None
    except Exception as exc:
        print(f"Warning: setuptools build failed, falling back: {exc}")
        return None
    finally:
        os.chdir(previous)
    path = os.path.join(wheel_dir, filename)
    return path if os.path.isfile(path) else None


def _build_wheel(
    src_root: str,
    wheel_dir: str,
    name: str,
    version: str,
    deps: Sequence[str],
) -> str:
    os.makedirs(wheel_dir, exist_ok=True)
    built = _build_wheel_with_setuptools(src_root, wheel_dir)
    if built:
        return built
    return _write_purelib_wheel(src_root, wheel_dir, name, version, deps)


def pack_project(
    root: Optional[str] = None,
    *,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> dict:
    """Pack the project at ``root``.

    Returns a dict with ``files``, ``wheel`` (path or planned name), and ``dry_run``.
    """
    root = os.path.abspath(root or os.getcwd())
    from nitrostack.cli.vendor import ensure_vendored_nitrostack
    ensure_vendored_nitrostack(root)
    files = collect_pack_files(root)
    name, version = _project_name_and_version(root)
    dist_name = _pep503_wheel_name(_normalize_dist_name(name))
    planned_wheel = f"{dist_name}-{version}-py3-none-any.whl"
    dest_dir = os.path.abspath(output_dir or os.path.join(root, "dist"))

    print("Files that would be packed:" if dry_run else "Packing files:")
    for rel in files:
        print(f"  {rel}")

    if dry_run:
        print(f"\nWould write: {os.path.join('dist', planned_wheel)}")
        print("Dry run — no artifact created.")
        return {"files": files, "wheel": os.path.join(dest_dir, planned_wheel), "dry_run": True}

    staging = tempfile.mkdtemp(prefix="nitrostack-pack-")
    wheel_tmp = tempfile.mkdtemp(prefix="nitrostack-wheel-")
    try:
        _copy_selected(root, staging, files)
        _ensure_env_example(staging)
        deps = requirements_from_project(root)
        write_requirements_txt(staging, deps)
        _ensure_pyproject(staging, name, version, deps)

        wheel_path = _build_wheel(staging, wheel_tmp, name, version, deps)
        extra = []
        env_example = os.path.join(staging, ".env.example")
        reqs = os.path.join(staging, "requirements.txt")
        extra.append((".env.example", env_example))
        extra.append(("requirements.txt", reqs))
        _inject_into_wheel(wheel_path, extra)

        if not _is_valid_wheel(wheel_path):
            raise RuntimeError(f"Built file is not a valid wheel: {wheel_path}")

        os.makedirs(dest_dir, exist_ok=True)
        final_path = os.path.join(dest_dir, os.path.basename(wheel_path))
        shutil.copy2(wheel_path, final_path)
        print(f"\nCreated wheel: {final_path}")
        return {"files": files, "wheel": final_path, "dry_run": False}
    finally:
        _rmtree(staging)
        _rmtree(wheel_tmp)
