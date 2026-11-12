"""File selection, staging, and cleanup for project packaging."""

from __future__ import annotations

import os
import shutil
import stat
from fnmatch import fnmatch
from typing import List, Sequence, Tuple

from nitrostack.cli._shared import EXCLUDE_DIR_NAMES


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
