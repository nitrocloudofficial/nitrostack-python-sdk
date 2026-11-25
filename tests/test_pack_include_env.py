"""Pack --include-env behaviour (merged from main)."""

from __future__ import annotations

from pathlib import Path
import zipfile

from nitrostack.cli.pack import pack_project


def _mini_project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app_module.py").write_text("NAME = 'demo'\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("nitrostack\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PORT=8000\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=should-never-pack\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep me\n", encoding="utf-8")
    return tmp_path


def test_pack_include_env_dry_run_lists_env_without_printing_contents(tmp_path: Path, capsys):
    project = _mini_project(tmp_path)
    (project / ".env.local").write_text("TOKEN=local-secret\n", encoding="utf-8")
    result = pack_project(str(project), dry_run=True, include_env=True)
    assert result["dry_run"] is True
    assert ".env" in result["files"]
    assert ".env.local" in result["files"]
    captured = capsys.readouterr().out
    assert "Warning: Including .env files in the wheel (use with caution!)" in captured
    assert ".env" in captured
    assert ".env.local" in captured
    assert "SECRET=should-never-pack" not in captured
    assert "TOKEN=local-secret" not in captured


def test_pack_include_env_puts_env_in_wheel_without_printing_contents(tmp_path: Path, capsys):
    project = _mini_project(tmp_path)
    result = pack_project(str(project), dry_run=False, include_env=True)
    captured = capsys.readouterr().out
    assert "Warning: Including .env files in the wheel (use with caution!)" in captured
    assert "SECRET=should-never-pack" not in captured

    wheel = Path(result["wheel"])
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        assert any(n == ".env" or n.endswith("/.env") for n in names)
        env_data = next(
            zf.read(n).decode("utf-8") for n in names if n == ".env" or n.endswith("/.env")
        )
        assert "SECRET=should-never-pack" in env_data