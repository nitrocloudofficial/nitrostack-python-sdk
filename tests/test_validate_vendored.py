"""Validate must not treat vendored SDK copies as project code."""

from pathlib import Path

from nitrostack.cli.validators import validate_project


def test_validate_skips_the_vendored_sdk(tmp_path: Path):
    """`init` vendors the SDK; its testing helpers apply @mcp_app inside functions."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "vendored"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        '    "nitrostack",\n'
        "]\n",
        encoding="utf-8",
    )
    sdk = tmp_path / "vendor" / "nitrostack" / "nitrostack" / "testing"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text(
        "from nitrostack import mcp_app, ServerConfig\n\n"
        "def create(module):\n"
        "    @mcp_app(module=module, server=ServerConfig(name='test'))\n"
        "    class TestApp:\n"
        "        pass\n"
        "    return TestApp\n",
        encoding="utf-8",
    )

    issues = validate_project(str(tmp_path))
    assert not [issue for issue in issues if "vendor" in issue.path], [i.format() for i in issues]