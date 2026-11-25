# Contributing to Nitrostack Python SDK

Welcome! This guide explains our development workflow and coding policies to help you contribute smoothly.

---

## Quick Start for Developers

```bash
# 1. Set up virtual environment and install dev dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Run tests
pytest tests

# 3. Check Python file-length policy
python scripts/check_file_lengths.py
```

---

## Python File-Length Policy

To keep modules focused and reviewable, we enforce a **500 physical-line limit** on Python files.

### The Rule
* **New files & standard files:** Must be **500 lines or fewer**.
* **Lines counted:** Total physical lines (including blanks, comments, docstrings, and multiline strings).
* **Scope:** Tracked `*.py` files in `nitrostack/`, `tests/`, `examples/`, and `scripts/`. Embedded templates in Python files count toward this limit.
* **Exempt:** Documentation (`.md`), static assets (HTML/CSS/JS), lockfiles, vendored packages, and generated build output.

---

## Working with Existing Oversized Files (The Ratchet)

A set of pre-existing legacy files exceed 500 lines. Instead of forcing immediate file splits, we use a **ratchet** tracked in [`.file_length_baseline.json`](.file_length_baseline.json):

1. **They cannot grow:** CI fails if any legacy file exceeds its current baseline number.
2. **They can shrink:** When you refactor or shrink an oversized file, lower its baseline automatically with:
   ```bash
   python scripts/check_file_lengths.py --update-baseline
   ```
   Then commit the updated `.file_length_baseline.json` with your PR.
3. **Graduation:** Once a legacy file reaches 500 lines or fewer, `--update-baseline` automatically removes it from the baseline. From that point on, it is capped at 500 lines forever.

> **Need an exception?** Any baseline increase is exceptional and must be explicitly justified and approved during code review.

---

## Continuous Integration (CI)

Our GitHub Actions workflow automatically checks every PR across Python 3.10, 3.11, and 3.12:
1. **File-length check:** Runs `python scripts/check_file_lengths.py`.
2. **Test suite:** Runs `pytest tests --cov=nitrostack --cov-report=term-missing --timeout=60`.
