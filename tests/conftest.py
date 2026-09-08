from __future__ import annotations

import pytest

from nitrostack.cli.skills import SkillsCloneError


@pytest.fixture(autouse=True)
def disable_live_skills_clone(monkeypatch):
    """Keep CLI tests off GitHub; test_cli_skills.py overrides this fixture."""

    def _blocked():
        raise SkillsCloneError("skills clone disabled in tests")

    monkeypatch.setattr("nitrostack.cli.skills.clone_skills_repo", _blocked)
