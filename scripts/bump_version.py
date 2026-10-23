#!/usr/bin/env python3
"""Bump and sync the SDK version in pyproject.toml and uv.lock.

Version sources of truth:
  - pyproject.toml  [project].version
  - uv.lock         [[package]] name = \"nitrostack\" (editable .)

Template app versions (0.1.0) and README CLI examples are not the SDK release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from typing import Optional, Sequence, Tuple

PYPI_JSON = "https://pypi.org/pypi/nitrostack/json"
_PROJECT_VERSION = re.compile(r'(?m)^(version = ")([^"]+)(")')
_LOCK_NITROSTACK = re.compile(
    r'(name = "nitrostack"\nversion = ")([^"]+)("\nsource = \{ editable = "\." \})',
)


def parse_x_y_z(raw: str) -> Tuple[int, int, int]:
    parts = (raw or "").strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Expected X.Y.Z version, got {raw!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_x_y_z(raw: str, kind: str) -> str:
    major, minor, patch = parse_x_y_z(raw)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump kind {kind!r}")


def version_key(raw: str) -> Tuple[int, int, int]:
    return parse_x_y_z(raw)


def read_project_version(pyproject_text: str) -> str:
    match = _PROJECT_VERSION.search(pyproject_text)
    if not match:
        raise ValueError("pyproject.toml has no version = \"...\" field")
    return match.group(2)


def set_project_version(pyproject_text: str, version: str) -> str:
    updated, n = _PROJECT_VERSION.subn(rf"\g<1>{version}\3", pyproject_text, count=1)
    if n != 1:
        raise ValueError("Could not replace [project].version in pyproject.toml")
    return updated


def set_lock_nitrostack_version(lock_text: str, version: str) -> str:
    updated, n = _LOCK_NITROSTACK.subn(rf"\g<1>{version}\3", lock_text, count=1)
    if n != 1:
        raise ValueError("Could not replace editable nitrostack version in uv.lock")
    return updated


def fetch_pypi(url: str = PYPI_JSON) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def pypi_latest_and_released(payload: dict) -> Tuple[Optional[str], set]:
    info = payload.get("info") or {}
    latest = info.get("version")
    released = set((payload.get("releases") or {}).keys())
    return latest, released


def plan_bump(git_ver: str, latest: Optional[str], released: set, kind: str) -> Optional[str]:
    """Return the next version to write, or None if a bump PR is not needed.

    Unpublished git versions are left for the publish workflow (git not on PyPI).
    Otherwise bump from max(git, PyPI latest) so a stale git version cannot
    collide with a manual upload (e.g. git 0.3.2, PyPI 0.3.5 → 0.3.6).
    """
    if git_ver not in released:
        return None
    base = git_ver
    if latest:
        base = git_ver if version_key(git_ver) >= version_key(latest) else latest
    return bump_x_y_z(base, kind)


def should_publish(git_ver: str, released: set) -> bool:
    return git_ver not in released


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def apply_version(root: str, version: str) -> None:
    pyproject = f"{root}/pyproject.toml"
    lock = f"{root}/uv.lock"
    _write(pyproject, set_project_version(_read(pyproject), version))
    _write(lock, set_lock_nitrostack_version(_read(lock), version))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write pyproject.toml and uv.lock. Prints the new version.",
    )
    parser.add_argument(
        "--if-unpublished",
        action="store_true",
        help="Exit 0 if the git version is not on PyPI (publish it); else exit 1.",
    )
    args = parser.parse_args(argv)

    git_ver = read_project_version(_read(f"{args.root}/pyproject.toml"))
    payload = fetch_pypi()
    latest, released = pypi_latest_and_released(payload)

    if args.if_unpublished:
        if should_publish(git_ver, released):
            print(git_ver)
            return 0
        print(f"skip: {git_ver} is already on PyPI", file=sys.stderr)
        return 1

    planned = plan_bump(git_ver, latest, released, args.bump)
    if planned is None:
        print(
            f"skip: {git_ver} is not on PyPI yet (publish this version first)",
            file=sys.stderr,
        )
        return 0
    if args.write:
        apply_version(args.root, planned)
    print(planned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
