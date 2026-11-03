"""Issue #76: real generated projects, CLI lifecycle, and distributable templates."""
from __future__ import annotations

import io
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile

import pytest

from nitrostack.cli.main import OFFICIAL_TEMPLATES, _prompt_template, _resolve_template

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "support" / "project_templates_probe.py"
TEMPLATES = [("python-enterprise-search", "enterprise-search"), ("python-code-mode", "code-mode")]


def run(args, cwd, *, python=None, installed=False):
    env = dict(os.environ, NITROSTACK_SKIP_UV_LOCK="1", PYTHONIOENCODING="utf-8")
    env["PYTHONPATH"] = "" if installed else str(ROOT)
    for key in ("NITRO_MCP_PROTOCOL_VERSION", "MCP_STATELESS", "MCP_TRANSPORT_TYPE"):
        env.pop(key, None)
    result = subprocess.run([str(python or sys.executable), str(PROBE), *args], cwd=cwd, env=env,
                            input="", text=True, encoding="utf-8", capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("choice,expected", [("4", "python-enterprise-search"), ("5", "python-code-mode"), ("python-enterprise-search", "python-enterprise-search"), ("python-code-mode", "python-code-mode")])
def test_new_template_prompt_choices(choice, expected, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(choice + "\n"))
    assert _prompt_template() == expected
    assert OFFICIAL_TEMPLATES[expected] in {"enterprise-search", "code-mode"}


@pytest.mark.parametrize("alias", ["enterprise-search", "code-mode"])
def test_unprefixed_template_aliases_are_not_accepted(alias):
    with pytest.raises(SystemExit):
        _resolve_template(alias)


def test_new_templates_in_cli_help(tmp_path):
    output = run(["cli", "init", "--help"], tmp_path)
    assert all(name in output for name, _ in TEMPLATES)


@pytest.mark.parametrize("name,kind", TEMPLATES)
def test_generated_project_lifecycle(name, kind, tmp_path):
    output = run(["cli", "init", "demo", "--template", name, "--skip-install"], tmp_path)
    assert "Preview widgets" not in output and "openNow" not in output
    project = tmp_path / "demo"
    for filename in ("pyproject.toml", "requirements.txt", ".python-version", "uv.toml", "README.md", ".env.example", ".env", ".gitignore", "main.py", "app_module.py"):
        assert (project / filename).is_file(), filename
    assert (project / ".python-version").read_text().strip() == "3.12"
    assert "No issues found" in run(["cli", "validate"], project)
    run([kind], project)
    if kind == "code-mode":
        run(["cleanup"], project)
    run(["cli", "upgrade", "--version", "0.3.2"], project)
    assert "No issues found" in run(["cli", "validate"], project)
    requirements = (project / "requirements.txt").read_text()
    assert "nitrostack==0.3.2" in requirements
    if kind == "code-mode":
        assert "quickjs>=1.19.4" in requirements
        assert "quickjs>=1.19.4" in (project / "pyproject.toml").read_text()
    run(["cli", "pack"], project)
    [wheel] = (project / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "main.py" in names and "app_module.py" in names
        assert "modules/inventory.py" in names
        assert not any(Path(name).name == ".env" for name in names)
        if kind == "code-mode":
            assert "scripts/sample-batch.js" in names
        metadata = archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
        assert "Requires-Dist: nitrostack" in metadata
        if kind == "code-mode":
            assert "quickjs>=1.19.4" in metadata.replace(" ", "")


@pytest.mark.parametrize("name,kind", TEMPLATES)
def test_generated_entrypoint_stdio_roundtrip_and_shutdown(name, kind, tmp_path):
    run(["cli", "init", "wire-demo", "--template", name, "--skip-install"], tmp_path)
    project = tmp_path / "wire-demo"
    env = dict(os.environ, PYTHONPATH=str(ROOT), MCP_TRANSPORT_TYPE="stdio",
               NITRO_MCP_PROTOCOL_VERSION="auto", PYTHONIOENCODING="utf-8")
    env.pop("MCP_STATELESS", None)
    process = subprocess.Popen([sys.executable, "main.py"], cwd=project, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8")
    reader = ThreadPoolExecutor(max_workers=1)
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "template-wire-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }

    def rpc(request_id, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                                       "params": {**params, "_meta": meta}}) + "\n")
        process.stdin.flush()
        line = reader.submit(process.stdout.readline).result(timeout=15)
        response = json.loads(line)
        assert response["id"] == request_id
        assert "error" not in response, response
        return response["result"]

    try:
        listed = rpc(1, "tools/list", {})
        names = {tool["name"] for tool in listed["tools"]}
        if kind == "enterprise-search":
            assert "search_tools" in names and "finance_list_invoices" not in names
            result = rpc(2, "tools/call", {"name": "call_tool", "arguments": {
                "name": "finance_list_invoices", "arguments": {"overdue_only": True},
            }})
            assert json.loads(result["content"][0]["text"])["invoices"][0]["id"] == "INV-001"
        else:
            assert names == {"search", "get_schema", "execute"}
            if sys.platform == "win32" and importlib.util.find_spec("quickjs") is None:
                result = rpc(2, "tools/call", {"name": "get_schema", "arguments": {"name": "inventory_check_stock"}})
                data = json.loads(result["content"][0]["text"])
                assert data["schemas"]["inventory_check_stock"]["inputSchema"]["required"] == ["sku"]
            else:
                result = rpc(2, "tools/call", {"name": "execute", "arguments": {
                    "code": (project / "scripts" / "sample-batch.js").read_text(),
                }})
                data = json.loads(result["content"][0]["text"])
                assert data["success"] is True and data["value"]["total_units"] == 18
        process.stdin.close()
        assert process.wait(timeout=15) == 0, process.stderr.read()
        assert process.stdout.read() == ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.shutdown()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


@pytest.fixture(scope="module")
def sdk_distribution(tmp_path_factory):
    base = tmp_path_factory.mktemp("sdk-templates")
    source = base / "source"
    source.mkdir()
    shutil.copytree(ROOT / "nitrostack", source / "nitrostack", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        if (ROOT / filename).is_file():
            shutil.copy2(ROOT / filename, source / filename)
    dist = base / "dist"
    dist.mkdir()
    code = "from setuptools.build_meta import build_wheel, build_sdist; import sys; dest = sys.argv[1]; build_wheel(dest); build_sdist(dest)"
    result = subprocess.run([sys.executable, "-c", code, str(dist)], cwd=source, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return base, next(dist.glob("*.whl")), next(dist.glob("*.tar.gz"))


def test_sdk_wheel_and_sdist_contain_complete_templates(sdk_distribution):
    _, wheel, sdist = sdk_distribution
    required = {f"nitrostack/templates/{kind}/{file}" for _, kind in TEMPLATES
                for file in (".python-version", ".env.example", ".gitignore", "README.md", "uv.toml", "requirements.txt", "pyproject.toml", "modules/inventory.py")}
    required.add("nitrostack/templates/code-mode/scripts/sample-batch.js")
    with zipfile.ZipFile(wheel) as archive:
        assert required <= set(archive.namelist())
    with tarfile.open(sdist) as archive:
        members = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
        assert required <= members


def test_installed_sdk_wheel_scaffolds_both_templates(sdk_distribution):
    base, wheel, _ = sdk_distribution
    envdir = base / "installed"
    # Use the base interpreter: managed Python 3.10 venvs can otherwise write
    # another venv's bin directory as their standard-library home.
    base_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    result = subprocess.run([str(base_python), "-m", "venv", "--system-site-packages", str(envdir)],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    python = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = dict(os.environ, PYTHONPATH="", PIP_DISABLE_PIP_VERSION_CHECK="1")
    result = subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "--ignore-installed", str(wheel)], env=env, cwd=base, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    # A venv created from another venv inherits the base interpreter's site,
    # not the parent's installed dependencies. Share only that dependency path;
    # the SDK wheel itself must resolve from the new environment below.
    site = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          env=env, cwd=base, text=True, capture_output=True, check=True, timeout=10)
    (Path(site.stdout.strip()) / "test_dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_paths()['purelib']!r})\n"
    )
    check = subprocess.run([str(python), "-c", "import nitrostack; print(nitrostack.__file__)"], env=env, cwd=base, text=True, capture_output=True, timeout=10)
    assert str(envdir).casefold() in check.stdout.casefold(), check.stdout + check.stderr
    for name, _ in TEMPLATES:
        target = base / name
        run(["cli", "init", name, "--template", name, "--skip-install"], base, python=python, installed=True)
        assert "No issues found" in run(["cli", "validate"], target, python=python, installed=True)


def test_upgrade_preserves_vendored_source_assignment():
    from nitrostack.cli.upgrade import find_current_spec, replace_nitrostack_spec
    from nitrostack.cli._shared import loads_toml
    for key in ("nitrostack", '\"nitrostack\"'):
        source = f'[tool.uv.sources]\n{key} = {{path = "vendor/nitrostack"}}\n'
        assert find_current_spec(source) is None
        assert replace_nitrostack_spec(source, "nitrostack==0.3.2") == (source, 0)
        project = '[project]\nname="demo"\ndependencies=["nitrostack", "quickjs>=1.19.4"]\n' + source
        updated, count = replace_nitrostack_spec(project, "nitrostack==0.3.2")
        parsed = loads_toml(updated)
        assert count == 1
        assert parsed["tool"]["uv"]["sources"]["nitrostack"] == {"path": "vendor/nitrostack"}
        assert parsed["project"]["dependencies"] == ["nitrostack==0.3.2", "quickjs>=1.19.4"]


def test_validation_checks_application_but_not_vendored_sdk(tmp_path):
    from nitrostack.cli.validators import validate_project
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\ndependencies=["nitrostack"]\n')
    sdk = tmp_path / "vendor" / "nitrostack"
    sdk.mkdir(parents=True)
    (sdk / "helper.py").write_text('from nitrostack import mcp_app\ndef helper():\n    @mcp_app(module=None)\n    class DynamicApp: pass\n')
    (tmp_path / "bad_application.py").write_text('from nitrostack import module\n@module(name="broken", imports=["not-a-class"])\nclass InvalidRoot: pass\n')
    issues = validate_project(str(tmp_path))
    assert any(issue.severity == "error" and issue.path == "bad_application.py" for issue in issues)
    assert not any("vendor" in (issue.path or "") for issue in issues)
