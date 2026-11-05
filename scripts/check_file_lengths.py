from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

DEFAULT_MAX_LINES = 500
SCOPED_DIRECTORIES = ("nitrostack", "tests", "examples", "scripts")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_FILE = REPO_ROOT / ".file_length_baseline.json"


class Violation(NamedTuple):
    path: str
    count: int
    allowed: int
    violation_type: str  # 'new_file', 'baseline_growth', or 'stale_baseline'
    message: str


def count_physical_lines(file_path: Path | str) -> int:
    """Count physical lines in a file, matching (Get-Content file).Count semantics.
    
    Includes blank lines, comments, docstrings, and lines inside multiline strings.
    A trailing newline does not add a phantom line.
    """
    path = Path(file_path)
    if not path.is_file():
        return 0
    with open(path, "rb") as f:
        content = f.read()
    return len(content.splitlines())


def get_tracked_python_files(
    repo_root: Path,
    scoped_dirs: Tuple[str, ...] = SCOPED_DIRECTORIES,
) -> List[str]:
    """Return relative POSIX paths of tracked Python files under scoped directories."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            text=True,
        )
        files = result.stdout.splitlines()
    except (subprocess.SubprocessError, FileNotFoundError):
        files = []
        for d in scoped_dirs:
            dir_path = repo_root / d
            if dir_path.is_dir():
                for root, _, filenames in os.walk(dir_path):
                    for filename in filenames:
                        rel = Path(root, filename).relative_to(repo_root).as_posix()
                        files.append(rel)

    matched = []
    for f in files:
        if not f.endswith(".py"):
            continue
        # Check if the file is within one of the scoped directories
        parts = Path(f).parts
        if parts and parts[0] in scoped_dirs:
            matched.append(Path(f).as_posix())

    return sorted(matched)


def load_baseline(baseline_path: Path) -> Dict[str, int]:
    """Load baseline mapping of file path -> allowed line count."""
    if not baseline_path.exists():
        return {}
    with open(baseline_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Ensure all paths are normalized POSIX strings
    return {Path(k).as_posix(): int(v) for k, v in data.items()}


def save_baseline(baseline_path: Path, baseline: Dict[str, int]) -> None:
    """Save baseline mapping to JSON sorted alphabetically."""
    sorted_baseline = {k: baseline[k] for k in sorted(baseline.keys())}
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(sorted_baseline, f, indent=2)
        f.write("\n")


def check_files(
    repo_root: Path,
    tracked_files: List[str],
    baseline: Dict[str, int],
    max_lines: int = DEFAULT_MAX_LINES,
) -> Tuple[List[Violation], Dict[str, int]]:
    """Check tracked files against the maximum line limit and ratchet baseline.

    Returns:
        (violations, new_ratcheted_baseline)
    """
    violations: List[Violation] = []
    ratcheted_baseline: Dict[str, int] = dict(baseline)

    # Track files currently present
    checked_paths = set(tracked_files)

    for rel_path in tracked_files:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            continue

        count = count_physical_lines(full_path)
        allowed = baseline.get(rel_path, max_lines)

        if count > allowed:
            if rel_path in baseline:
                vtype = "baseline_growth"
                msg = (
                    f"File grew above baseline: {rel_path} has {count} lines "
                    f"(allowed baseline: {allowed})"
                )
            else:
                vtype = "new_file"
                msg = (
                    f"File exceeds line limit: {rel_path} has {count} lines "
                    f"(allowed maximum: {allowed})"
                )
            violations.append(
                Violation(
                    path=rel_path,
                    count=count,
                    allowed=allowed,
                    violation_type=vtype,
                    message=msg,
                )
            )
        elif rel_path in baseline:
            # Ratchet mechanism: file shrank below baseline!
            if count <= max_lines:
                # File reached or dropped below standard threshold -> remove from baseline
                del ratcheted_baseline[rel_path]
            elif count < allowed:
                # File shrank, lower the baseline
                ratcheted_baseline[rel_path] = count

    # Also clean up any baseline entries for files that no longer exist
    for baseline_file in list(ratcheted_baseline.keys()):
        if baseline_file not in checked_paths:
            del ratcheted_baseline[baseline_file]

    return violations, ratcheted_baseline


def run_check(
    repo_root: Path,
    baseline_path: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    update_baseline: bool = False,
) -> int:
    """Run checker and report results.

    Returns exit code (0 for success, 1 for violations).
    """
    tracked = get_tracked_python_files(repo_root)
    baseline = load_baseline(baseline_path)
    violations, ratcheted_baseline = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=max_lines,
    )

    ratchet_needed = ratcheted_baseline != baseline

    if violations:
        print(f"FAILED: Found {len(violations)} Python file-length violation(s):")
        for v in violations:
            print(f"  [{v.violation_type}] {v.path}: count={v.count}, allowed={v.allowed}")
            print(f"    -> {v.message}")

    if ratchet_needed:
        if update_baseline:
            save_baseline(baseline_path, ratcheted_baseline)
            print(f"Updated baseline file at {baseline_path} with ratcheted counts.")
        else:
            print("\nNOTE: Baseline ratchet improvement detected:")
            for k, old_count in baseline.items():
                new_count = ratcheted_baseline.get(k)
                if new_count is None:
                    print(f"  {k}: was {old_count}, now <= {max_lines} (entry removed from baseline)")
                elif new_count < old_count:
                    print(f"  {k}: was {old_count}, lowered to {new_count}")
            print("Run with --update-baseline to update the baseline file.")

    if violations:
        return 1

    print(
        f"SUCCESS: All {len(tracked)} tracked Python files in scope satisfy file-length limits "
        f"(max {max_lines} lines or within baseline ratchet)."
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Python file lengths against limit (500 lines) and baseline ratchet."
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Update the baseline file if files have shrunk or reached <= 500 lines",
    )

    args = parser.parse_args(argv)

    return run_check(
        repo_root=REPO_ROOT,
        baseline_path=DEFAULT_BASELINE_FILE,
        max_lines=DEFAULT_MAX_LINES,
        update_baseline=args.update_baseline,
    )


if __name__ == "__main__":
    sys.exit(main())