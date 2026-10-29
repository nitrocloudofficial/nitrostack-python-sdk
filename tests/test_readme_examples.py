"""README code blocks marked with <!-- readme-test --> must run as written."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"
BLOCKS = re.findall(r"<!-- readme-test -->\n```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)


def test_readme_has_marked_examples():
    assert BLOCKS


@pytest.mark.parametrize("source", BLOCKS)
def test_readme_example_runs(source):
    namespace: dict = {"__name__": "readme_example"}
    exec(compile(source, "README.md", "exec"), namespace)
    tests = [fn for name, fn in namespace.items() if name.startswith("test_") and callable(fn)]
    assert tests
    for fn in tests:
        asyncio.run(fn())
