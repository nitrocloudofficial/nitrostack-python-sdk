"""In-place nitrostack version upgrades for `nitrostack-py upgrade`."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional, Tuple

from packaging.version import InvalidVersion, Version

from nitrostack.cli._shared import (
    add_to_toml_string_array,
    find_toml_array_inner,
    loads_toml,
    read_text,
    write_text_atomic,
)
from nitrostack.cli.skills import upgrade_agent_skills

PYPI_JSON = "https://pypi.org/pypi/nitrostack/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/nitrostack/{version}/json"

# Name must not be a prefix of nitrostack-studio / nitrostack_extras / nitrostack.contrib.
_DEP_RE = re.compile(
    r'(["\']?)(nitrostack)(?![\w.-])((?:\s*(?:===|==|!=|~=|>=|<=|>|<)\s*[^"\'\s,#]+)?)(\1)',
    re.IGNORECASE,
)


class UpgradeError(RuntimeError):
    pass


def fetch_latest_nitrostack_version(timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        PYPI_JSON,
        headers={"Accept": "application/json", "User-Agent": "nitrostack-py"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise UpgradeError(f"PyPI returned invalid JSON: {exc}") from exc
    except urllib.error.URLError as exc:
        raise UpgradeError(
            f"Could not reach PyPI to determine the latest nitrostack version: {exc}\n"
            "Check your network connection, then retry."
        ) from exc
    version = (payload.get("info") or {}).get("version")
    if not version:
        raise UpgradeError("PyPI response did not include a version for nitrostack.")
    return str(version)


def verify_nitrostack_version(version: str, timeout: float = 15.0) -> None:
    req = urllib.request.Request(
        PYPI_VERSION_JSON.format(version=version),
        headers={"Accept": "application/json", "User-Agent": "nitrostack-py"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise UpgradeError(f"nitrostack=={version} was not found on PyPI.")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpgradeError(
                f"nitrostack=={version} was not found on PyPI. "
                "Check the version string and retry."
            ) from exc
        raise UpgradeError(f"PyPI lookup for nitrostack=={version} failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise UpgradeError(f"Could not reach PyPI to verify nitrostack=={version}: {exc}") from exc


def find_current_spec(text: str) -> Optional[str]:
    match = _DEP_RE.search(text)
    if not match:
        return None
    spec = (match.group(2) + (match.group(3) or "")).strip()
    return spec


def replace_nitrostack_spec(text: str, spec: str) -> Tuple[str, int]:
    def _sub(match: re.Match) -> str:
        quote = match.group(1) or ""
        return f"{quote}{spec}{quote}"

    return _DEP_RE.subn(_sub, text)


def _add_to_pyproject_dependencies(text: str, spec: str) -> str:
    if find_toml_array_inner(text, "dependencies"):
        updated = add_to_toml_string_array(text, spec, "dependencies")
        try:
            loads_toml(updated)
        except Exception as exc:
            raise UpgradeError(
                f"Refusing to write an invalid pyproject.toml after adding {spec}: {exc}"
            ) from exc
        return updated
    project = re.search(r"^\[project\][^\[]*", text, re.MULTILINE | re.DOTALL)
    if not project:
        raise UpgradeError(
            "pyproject.toml has no [project] table. Add one (or add nitrostack to "
            "requirements.txt) before running upgrade."
        )
    insert_at = project.end()
    block = f'\ndependencies = [\n    "{spec}",\n]\n'
    return text[:insert_at] + block + text[insert_at:]


def _declared_version(spec: Optional[str]) -> Optional[Version]:
    if not spec:
        return None
    match = re.search(r"(?:===|==|!=|~=|>=|<=|>|<)\s*([0-9A-Za-z][0-9A-Za-z._+-]*)", spec)
    if not match:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def _commit_file_changes(changes: list) -> list:
    """Write all files atomically; roll back earlier writes if a later one fails."""
    written = []
    try:
        for change in changes:
            write_text_atomic(change["path"], change["text"])
            written.append(change)
            print(f"Updated {change['file']}")
        return [change["file"] for change in written]
    except Exception:
        for change in reversed(written):
            original = change.get("original")
            if original is None:
                continue
            try:
                write_text_atomic(change["path"], original)
            except OSError:
                pass
        raise


def upgrade_project(
    root: Optional[str] = None,
    *,
    version: Optional[str] = None,
    dry_run: bool = False,
    verify: bool = True,
    allow_downgrade: bool = False,
) -> dict:
    """Update the nitrostack dependency spec in pyproject.toml (and requirements.txt if present)."""
    root = os.path.abspath(root or os.getcwd())
    pyproject = os.path.join(root, "pyproject.toml")
    requirements = os.path.join(root, "requirements.txt")

    if not os.path.isfile(pyproject) and not os.path.isfile(requirements):
        raise UpgradeError(
            "No pyproject.toml or requirements.txt found in the current directory.\n"
            "Run this command from a NitroStack project, or create pyproject.toml first."
        )

    target = version or fetch_latest_nitrostack_version()
    if version and verify:
        verify_nitrostack_version(target)

    new_spec = f"nitrostack=={target}" if version else f"nitrostack>={target}"
    changes = []
    original_pyproject = read_text(pyproject) if os.path.isfile(pyproject) else None
    original_reqs = read_text(requirements) if os.path.isfile(requirements) else None

    current_specs = []
    if original_pyproject is not None:
        current_specs.append(find_current_spec(original_pyproject))
    if original_reqs is not None:
        current_specs.append(find_current_spec(original_reqs))

    try:
        target_ver = Version(target)
    except InvalidVersion as exc:
        raise UpgradeError(f"Invalid target version '{target}': {exc}") from exc

    if not allow_downgrade:
        for current in current_specs:
            declared = _declared_version(current)
            if declared is not None and target_ver < declared:
                raise UpgradeError(
                    f"Target {target} is older than the currently declared {current}. "
                    "Re-run with --allow-downgrade if you intend to downgrade."
                )

    if original_pyproject is not None:
        current = find_current_spec(original_pyproject)
        updated, n = replace_nitrostack_spec(original_pyproject, new_spec)
        if n == 0:
            updated = _add_to_pyproject_dependencies(original_pyproject, new_spec)
            current = current or "(missing)"
        if updated != original_pyproject:
            try:
                loads_toml(updated)
            except Exception as exc:
                raise UpgradeError(f"Refusing to write an invalid pyproject.toml: {exc}") from exc
        changes.append(
            {
                "file": "pyproject.toml",
                "path": pyproject,
                "from": current or "(missing)",
                "to": new_spec,
                "text": updated,
                "original": original_pyproject,
            }
        )

    if original_reqs is not None and find_current_spec(original_reqs):
        current = find_current_spec(original_reqs)
        updated, n = replace_nitrostack_spec(original_reqs, new_spec)
        if n:
            changes.append(
                {
                    "file": "requirements.txt",
                    "path": requirements,
                    "from": current,
                    "to": new_spec,
                    "text": updated,
                    "original": original_reqs,
                }
            )

    if not changes:
        raise UpgradeError(
            "Could not find a nitrostack dependency to update.\n"
            "Add `nitrostack` to [project].dependencies in pyproject.toml and retry."
        )

    print("NITROSTACK — Upgrade" + (" (dry run)" if dry_run else ""))
    print(f"Target version: {target}")
    print(f"Resulting spec: {new_spec}")
    for change in changes:
        print(f"  {change['file']}: {change['from']} → {change['to']}")

    if dry_run:
        print("\nDry run — no files modified.")
        skills = upgrade_agent_skills(root, dry_run=True)
        return {
            "version": target,
            "spec": new_spec,
            "changes": changes,
            "dry_run": True,
            "written": [],
            "skills": skills,
        }

    written = _commit_file_changes(changes)

    print(f"\nUpgrade complete. nitrostack dependency is now {new_spec}.")
    print("Run `nitrostack-py install` to install the new version.")
    skills = upgrade_agent_skills(root, dry_run=False)
    return {
        "version": target,
        "spec": new_spec,
        "changes": changes,
        "dry_run": False,
        "written": written,
        "skills": skills,
    }
