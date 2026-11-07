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

# Keep the original import surface while implementations live by responsibility.
from nitrostack.cli._pack_files import (
    _EXCLUDE_DIR_NAMES, _EXCLUDE_NAME_GLOBS, _EXCLUDE_SUFFIXES,
    _collect_staging_bytes, _copy_selected, _is_secret_env,
    _load_gitignore_patterns, _matches_gitignore, _rmtree, _should_exclude,
    collect_pack_files,
)
from nitrostack.cli._pack_metadata import (
    ENV_EXAMPLE_STUB, _PYPROJECT_TEMPLATE, _discover_py_modules_and_packages,
    _ensure_env_example, _ensure_pyproject, _normalize_dist_name,
    _parse_pyproject_dependencies, _parse_pyproject_field, _parse_requirements,
    _pep503_wheel_name, _project_name_and_version, _read_text, _requirement_name,
    _toml_list, requirements_from_project, write_requirements_txt,
)
from nitrostack.cli import _pack_wheel
from nitrostack.cli._pack_wheel import (
    _inject_into_wheel, _is_valid_wheel, _setuptools_build_wheel,
    _sha256_record, _write_purelib_wheel,
)


def _build_wheel_with_setuptools(src_root: str, wheel_dir: str) -> Optional[str]:
    """Resolve the backend callback here so existing facade patches still apply."""
    return _pack_wheel._build_wheel_with_setuptools(
        src_root, wheel_dir, build_backend=_setuptools_build_wheel,
    )


def _build_wheel(
    src_root: str,
    wheel_dir: str,
    name: str,
    version: str,
    deps: Sequence[str],
) -> str:
    return _pack_wheel._build_wheel(
        src_root, wheel_dir, name, version, deps,
        build_with_setuptools=_build_wheel_with_setuptools,
        write_purelib=_write_purelib_wheel,
    )


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
