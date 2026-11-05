"""Tests for the dependency-free file-length checker and baseline ratchet."""

import sys
from pathlib import Path

# Ensure repo root is in sys.path so scripts can be imported cleanly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from scripts.check_file_lengths import (
    check_files,
    count_physical_lines,
    load_baseline,
    run_check,
    save_baseline,
)


def test_count_physical_lines(tmp_path: Path):
    file_path = tmp_path / "sample.py"
    # Content with blank lines, comments, docstrings, multiline strings
    content = '"""Module docstring.\nLine 2.\n"""\n\n# A comment\nx = 1\n'
    file_path.write_text(content, encoding="utf-8")
    assert count_physical_lines(file_path) == 6

    # Trailing newline does not add a phantom line
    file_path_no_newline = tmp_path / "no_newline.py"
    file_path_no_newline.write_text("a = 1\nb = 2", encoding="utf-8")
    assert count_physical_lines(file_path_no_newline) == 2

    # Non-existent file
    assert count_physical_lines(tmp_path / "non_existent.py") == 0


def test_file_exactly_at_500_lines(tmp_path: Path):
    repo_root = tmp_path
    py_file = repo_root / "nitrostack" / "boundary.py"
    py_file.parent.mkdir(parents=True)
    # Write exactly 500 lines
    py_file.write_text("\n".join(f"x_{i} = {i}" for i in range(500)), encoding="utf-8")

    tracked = ["nitrostack/boundary.py"]
    baseline = {}

    violations, ratcheted = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=500,
    )
    assert len(violations) == 0
    assert ratcheted == {}


def test_new_file_over_500_lines(tmp_path: Path):
    repo_root = tmp_path
    py_file = repo_root / "nitrostack" / "large.py"
    py_file.parent.mkdir(parents=True)
    # Write 501 lines
    py_file.write_text("\n".join(f"x_{i} = {i}" for i in range(501)), encoding="utf-8")

    tracked = ["nitrostack/large.py"]
    baseline = {}

    violations, _ = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=500,
    )
    assert len(violations) == 1
    v = violations[0]
    assert v.path == "nitrostack/large.py"
    assert v.count == 501
    assert v.allowed == 500
    assert v.violation_type == "new_file"
    assert "File exceeds line limit" in v.message


def test_baseline_growth(tmp_path: Path):
    repo_root = tmp_path
    py_file = repo_root / "nitrostack" / "legacy.py"
    py_file.parent.mkdir(parents=True)
    # File has 600 lines, baseline is 550
    py_file.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    tracked = ["nitrostack/legacy.py"]
    baseline = {"nitrostack/legacy.py": 550}

    violations, _ = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=500,
    )
    assert len(violations) == 1
    v = violations[0]
    assert v.path == "nitrostack/legacy.py"
    assert v.count == 600
    assert v.allowed == 550
    assert v.violation_type == "baseline_growth"
    assert "File grew above baseline" in v.message


def test_file_below_its_baseline_ratchet(tmp_path: Path):
    repo_root = tmp_path
    py_file = repo_root / "nitrostack" / "legacy.py"
    py_file.parent.mkdir(parents=True)
    # File previously was 700 lines (in baseline), now shrunk to 620 lines (> 500)
    py_file.write_text("\n".join(f"x_{i} = {i}" for i in range(620)), encoding="utf-8")

    tracked = ["nitrostack/legacy.py"]
    baseline = {"nitrostack/legacy.py": 700}

    violations, ratcheted = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=500,
    )
    assert len(violations) == 0
    # Ratchet lowered the baseline
    assert ratcheted["nitrostack/legacy.py"] == 620


def test_file_drops_to_or_below_500_removed_from_baseline(tmp_path: Path):
    repo_root = tmp_path
    py_file = repo_root / "nitrostack" / "legacy.py"
    py_file.parent.mkdir(parents=True)
    # File was 600 in baseline, now shrunk to 500 lines
    py_file.write_text("\n".join(f"x_{i} = {i}" for i in range(500)), encoding="utf-8")

    tracked = ["nitrostack/legacy.py"]
    baseline = {"nitrostack/legacy.py": 600}

    violations, ratcheted = check_files(
        repo_root=repo_root,
        tracked_files=tracked,
        baseline=baseline,
        max_lines=500,
    )
    assert len(violations) == 0
    # Ratchet removed it from baseline because it now satisfies the standard 500 threshold
    assert "nitrostack/legacy.py" not in ratcheted


def test_baseline_save_and_load(tmp_path: Path):
    baseline_path = tmp_path / "baseline.json"
    data = {"b.py": 600, "a.py": 700}
    save_baseline(baseline_path, data)

    # Should be saved sorted by key
    loaded = load_baseline(baseline_path)
    assert list(loaded.keys()) == ["a.py", "b.py"]
    assert loaded == {"a.py": 700, "b.py": 600}


def test_run_check_flow_and_update_baseline(tmp_path: Path, capsys):
    repo_root = tmp_path
    baseline_path = repo_root / ".file_length_baseline.json"

    # Create tracked files under nitrostack and tests
    f1 = repo_root / "nitrostack" / "f1.py"
    f1.parent.mkdir(parents=True)
    f1.write_text("\n".join(f"x_{i} = {i}" for i in range(550)), encoding="utf-8")

    save_baseline(baseline_path, {"nitrostack/f1.py": 600})

    # Run check without update: should succeed (550 <= 600) and notify ratchet
    code = run_check(repo_root=repo_root, baseline_path=baseline_path, update_baseline=False)
    assert code == 0
    captured = capsys.readouterr().out
    assert "Baseline ratchet improvement detected" in captured

    # Run check with update: should update baseline
    code = run_check(repo_root=repo_root, baseline_path=baseline_path, update_baseline=True)
    assert code == 0
    updated_baseline = load_baseline(baseline_path)
    assert updated_baseline["nitrostack/f1.py"] == 550

    # Grow file to 580: should fail
    f1.write_text("\n".join(f"x_{i} = {i}" for i in range(580)), encoding="utf-8")
    code = run_check(repo_root=repo_root, baseline_path=baseline_path, update_baseline=False)
    assert code == 1
    captured = capsys.readouterr().out
    assert "FAILED: Found 1 Python file-length violation(s)" in captured
    assert "baseline_growth" in captured
