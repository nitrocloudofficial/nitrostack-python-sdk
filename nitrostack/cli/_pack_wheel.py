"""Wheel assembly, backend fallback, and archive integrity for project packaging."""

from __future__ import annotations

import os
import zipfile
from typing import Callable, Optional, Sequence, Tuple

from nitrostack.cli._pack_files import _collect_staging_bytes
from nitrostack.cli._pack_metadata import _normalize_dist_name, _pep503_wheel_name


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


def _build_wheel_with_setuptools(
    src_root: str, wheel_dir: str, *, build_backend: Callable[[str], str],
) -> Optional[str]:
    """Use the setuptools PEP 517 backend when it is importable."""
    os.makedirs(wheel_dir, exist_ok=True)
    previous = os.getcwd()
    try:
        os.chdir(src_root)
        filename = build_backend(wheel_dir)
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
    *,
    build_with_setuptools: Callable[[str, str], Optional[str]],
    write_purelib: Callable[[str, str, str, str, Sequence[str]], str],
) -> str:
    os.makedirs(wheel_dir, exist_ok=True)
    built = build_with_setuptools(src_root, wheel_dir)
    if built:
        return built
    return write_purelib(src_root, wheel_dir, name, version, deps)
