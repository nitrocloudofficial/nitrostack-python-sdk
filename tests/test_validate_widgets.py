import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nitrostack.cli.validators import _collect_widget_routes, validate_project, validate_widgets

TOOL = (
    "from nitrostack import tool, widget\n\n"
    "@tool(name='show')\n"
    "@widget('card')\n"
    "async def show(self, input, context):\n"
    "    return {}\n"
)


def _project(tmp_path: Path, source: str = TOOL) -> Path:
    (tmp_path / "tools.py").write_text(source, encoding="utf-8")
    return tmp_path


def _write_html(root: Path, route: str, html: str) -> None:
    out = root / "widgets" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{route}.html").write_text(html, encoding="utf-8")


def test_missing_widget_html_is_a_warning(tmp_path: Path):
    issues = validate_widgets(str(_project(tmp_path)))
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "file is missing" in issues[0].message
    assert "card.html" in issues[0].message
    assert "ui://widget/card.html" in issues[0].message
    assert issues[0].path == "tools.py"


def test_empty_widget_html_is_a_warning(tmp_path: Path):
    _write_html(_project(tmp_path), "card", "  \n")
    issues = validate_widgets(str(tmp_path))
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "file is empty" in issues[0].message


def test_valid_widget_html_passes(tmp_path: Path):
    _write_html(_project(tmp_path), "card", "<html></html>")
    assert validate_widgets(str(tmp_path)) == []


def test_widget_options_route_is_collected(tmp_path: Path):
    src = "from nitrostack import WidgetOptions, widget\n\n@widget(WidgetOptions(route='chart'))\ndef f(): ...\n"
    issues = validate_widgets(str(_project(tmp_path, src)))
    assert len(issues) == 1
    assert "'chart'" in issues[0].message


def test_invalid_route_is_reported_not_resolved(tmp_path: Path):
    issues = validate_widgets(str(_project(tmp_path, "@widget('../evil')\ndef f(): ...\n")))
    assert len(issues) == 1
    assert "not a single alphanumeric path segment" in issues[0].message


def test_duplicate_routes_are_reported_once(tmp_path: Path):
    (tmp_path / "a.py").write_text(TOOL, encoding="utf-8")
    (tmp_path / "b.py").write_text(TOOL, encoding="utf-8")
    assert len(validate_widgets(str(tmp_path))) == 1


def test_vendored_sdk_is_ignored(tmp_path: Path):
    vendored = tmp_path / "vendor" / "nitrostack"
    vendored.mkdir(parents=True)
    (vendored / "x.py").write_text(TOOL, encoding="utf-8")
    assert validate_widgets(str(tmp_path)) == []


def test_project_without_widgets_has_no_widget_issues(tmp_path: Path):
    (tmp_path / "plain.py").write_text("x = 1\n", encoding="utf-8")
    assert validate_widgets(str(tmp_path)) == []


def test_validate_project_reports_widget_warnings_only(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    _project(tmp_path)
    issues = [i for i in validate_project(str(tmp_path)) if "widget route" in i.message]
    assert len(issues) == 1
    assert issues[0].severity == "warning"

HELPER = (
    "from nitrostack import WidgetOptions, widget\n\n"
    "def pizzaz_widget(route: str) -> WidgetOptions:\n"
    "    return WidgetOptions(route=route, prefers_border=True)\n\n"
    "class helpers:\n"
    "    @staticmethod\n"
    "    def make(route):\n"
    "        return WidgetOptions(route=route)\n\n"
    "class T:\n"
    "    @widget(pizzaz_widget('pizza-map'))\n"
    "    def a(self): ...\n\n"
    "    @widget(helpers.make(\"pizza-list\"))\n"
    "    def b(self): ...\n\n"
    "    @widget(pizzaz_widget(route='pizza-shop', extra=1))\n"
    "    def c(self): ...\n"
)


def test_helper_wrapped_routes_are_collected(tmp_path: Path):
    issues = validate_widgets(str(_project(tmp_path, HELPER)))
    assert sorted(i.message.split("'")[1] for i in issues) == ["pizza-list", "pizza-map", "pizza-shop"]
    assert all("file is missing" in i.message for i in issues)


def test_helper_wrapped_route_with_html_passes(tmp_path: Path):
    _project(tmp_path, HELPER)
    for route in ("pizza-map", "pizza-list", "pizza-shop"):
        _write_html(tmp_path, route, "<html></html>")
    assert validate_widgets(str(tmp_path)) == []


def test_route_is_read_from_the_argument_that_feeds_it(tmp_path: Path):
    src = (
        "from nitrostack import WidgetOptions, widget\n"
        "def helper(flag, route): return WidgetOptions(route=route)\n"
        "@widget(helper(True, 'second'))\ndef a(): ...\n"
        "@widget(helper('first', 'third'))\ndef b(): ...\n"
        "@widget(helper(flag=False, route='by-keyword'))\ndef c(): ...\n"
    )
    assert sorted(_routes(tmp_path, {"tools.py": src})) == ["by-keyword", "second", "third"]


def test_unrelated_helper_call_is_not_taken_as_a_route(tmp_path: Path):
    # Not defined in the project and never builds a WidgetOptions: no false warning.
    src = "@widget(something('x'))\ndef f(): ...\n"
    assert validate_widgets(str(_project(tmp_path, src))) == []
    src = "def something(name): return {'n': name}\n@widget(something('x'))\ndef f(): ...\n"
    assert validate_widgets(str(_project(tmp_path, src))) == []


def test_helper_defined_in_another_file_is_found(tmp_path: Path):
    (tmp_path / "helpers.py").write_text(
        "from nitrostack import WidgetOptions\ndef panel(route): return WidgetOptions(route=route)\n",
        encoding="utf-8",
    )
    src = "from helpers import panel\n@widget(panel('chart'))\ndef f(): ...\n"
    issues = validate_widgets(str(_project(tmp_path, src)))
    assert len(issues) == 1 and "'chart'" in issues[0].message


def test_widget_options_positional_route_is_collected(tmp_path: Path):
    src = "from nitrostack import WidgetOptions, widget\n@widget(WidgetOptions('chart'))\ndef f(): ...\n"
    issues = validate_widgets(str(_project(tmp_path, src)))
    assert len(issues) == 1 and "'chart'" in issues[0].message


def test_invalid_helper_route_is_reported(tmp_path: Path):
    src = "def helper(r): return WidgetOptions(route=r)\n@widget(helper('../evil'))\ndef f(): ...\n"
    issues = validate_widgets(str(_project(tmp_path, src)))
    assert len(issues) == 1
    assert "not a single alphanumeric path segment" in issues[0].message


TEMPLATES = Path(__file__).resolve().parents[1] / "nitrostack" / "templates"


@pytest.mark.parametrize("template, expected", [
    ("pizzaz", {"pizza-map", "pizza-list", "pizza-shop"}),
    ("starter", {"calculator-result"}),
    ("flight-booking", {
        "flight-search-results", "flight-details", "airport-search",
        "order-summary", "seat-selection", "order-cancellation",
    }),
])
def test_shipped_templates_report_every_widget_route(template, expected):
    routes = {route for route, _ in _collect_widget_routes(str(TEMPLATES / template))}
    assert routes == expected


def test_pizzaz_template_warns_when_a_widget_html_is_removed(tmp_path: Path):
    project = tmp_path / "pizzaz"
    shutil.copytree(TEMPLATES / "pizzaz", project)
    assert validate_widgets(str(project)) == []
    (project / "widgets" / "out" / "pizza-list.html").unlink()
    issues = validate_widgets(str(project))
    assert len(issues) == 1
    assert "'pizza-list'" in issues[0].message and "file is missing" in issues[0].message


def test_cli_validate_checks_pizzaz_helper_wrapped_widgets(tmp_path: Path):
    project = tmp_path / "pizzaz"
    shutil.copytree(TEMPLATES / "pizzaz", project)

    def validate():
        return subprocess.run(
            [sys.executable, "-m", "nitrostack.cli.main", "validate"],
            cwd=project, env={**os.environ, "PYTHONPATH": str(TEMPLATES.parents[1])},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )

    clean = validate()
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "widget route" not in clean.stdout

    (project / "widgets" / "out" / "pizza-list.html").unlink()
    broken = validate().stdout
    assert "widget route 'pizza-list'" in broken and "file is missing" in broken
    assert "pizza-map" not in broken and "pizza-shop" not in broken


def _routes(tmp_path: Path, files: dict) -> list:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [route for route, _ in _collect_widget_routes(str(tmp_path))]


FACTORY = "from nitrostack import WidgetOptions\ndef panel(route): return WidgetOptions(route=route)\n"


def test_argument_that_does_not_feed_route_is_not_a_route(tmp_path: Path):
    # panel("dashboard") builds the "card" widget; dashboard.html is never used.
    src = (
        "from nitrostack import WidgetOptions, widget\n"
        "def panel(name, description=None):\n"
        "    return WidgetOptions(route='card', description=description)\n"
        "@widget(panel('dashboard'))\ndef f(): ...\n"
    )
    assert _routes(tmp_path, {"tools.py": src}) == ["card"]
    assert validate_widgets(str(tmp_path))[0].message.startswith("widget route 'card'")
    assert len(validate_widgets(str(tmp_path))) == 1


def test_reassigned_or_forwarded_to_something_else_is_not_a_factory(tmp_path: Path):
    src = (
        "from nitrostack import WidgetOptions, widget\n"
        "def fixed(route):\n    route = 'always-this'\n    return WidgetOptions(route=route)\n"
        "def other(route): return WidgetOptions(route='card', description=route)\n"
        "@widget(fixed('x'))\ndef a(): ...\n@widget(other('y'))\ndef b(): ...\n"
    )
    assert _routes(tmp_path, {"tools.py": src}) == ["card"]


def test_same_helper_name_in_another_module_is_not_a_factory(tmp_path: Path):
    files = {
        "factory.py": FACTORY,
        "unrelated.py": (
            "from nitrostack import widget\n"
            "def panel(name): return {'name': name}\n"
            "@widget(panel('not-a-route'))\ndef f(): ...\n"
        ),
        "undefined.py": "from nitrostack import widget\n@widget(panel('also-not'))\ndef g(): ...\n",
        "other_lib.py": (
            "from some_library import panel\nfrom nitrostack import widget\n"
            "@widget(panel('third-party'))\ndef h(): ...\n"
        ),
    }
    assert _routes(tmp_path, files) == []


def test_factory_is_resolved_through_the_calling_modules_imports(tmp_path: Path):
    files = {
        "factory.py": FACTORY,
        "plain.py": "from factory import panel\nfrom nitrostack import widget\n@widget(panel('plain'))\ndef a(): ...\n",
        "aliased.py": "from factory import panel as build\nfrom nitrostack import widget\n@widget(build('aliased'))\ndef b(): ...\n",
        "module.py": "import factory as fx\nfrom nitrostack import widget\n@widget(fx.panel('via-module'))\ndef c(): ...\n",
        "pkg/__init__.py": "",
        "pkg/helpers.py": FACTORY,
        "pkg/tools.py": "from .helpers import panel\nfrom nitrostack import widget\n@widget(panel('relative'))\ndef d(): ...\n",
        "pkg/sub.py": "from pkg import helpers\nfrom nitrostack import widget\n@widget(helpers.panel('pkg-module'))\ndef e(): ...\n",
    }
    assert sorted(_routes(tmp_path, files)) == ["aliased", "pkg-module", "plain", "relative", "via-module"]


def test_a_local_definition_wins_over_an_import_of_the_same_name(tmp_path: Path):
    files = {
        "factory.py": FACTORY,
        "tools.py": (
            "from factory import panel\nfrom nitrostack import widget\n"
            "def panel(name): return {'name': name}\n"
            "@widget(panel('shadowed'))\ndef f(): ...\n"
        ),
    }
    assert _routes(tmp_path, files) == []


def test_a_factory_that_forwards_to_another_factory_is_found(tmp_path: Path):
    src = (
        "from nitrostack import WidgetOptions, widget\n"
        "def base(route): return WidgetOptions(route=route)\n"
        "def wrapped(r): return base(r)\n"
        "@widget(wrapped('deep'))\ndef f(): ...\n"
    )
    assert _routes(tmp_path, {"tools.py": src}) == ["deep"]


def test_unimported_widget_options_alias_and_attribute_forms(tmp_path: Path):
    src = (
        "import nitrostack\nfrom nitrostack import WidgetOptions as WO, widget\n"
        "@widget(WO(route='aliased-options'))\ndef a(): ...\n"
        "@nitrostack.widget(nitrostack.WidgetOptions('attribute-form'))\ndef b(): ...\n"
    )
    assert sorted(_routes(tmp_path, {"tools.py": src})) == ["aliased-options", "attribute-form"]


def test_a_file_that_does_not_parse_still_yields_its_literal_routes(tmp_path: Path):
    src = "@widget('legacy')\ndef f(:\n    pass\nWidgetOptions(route='legacy-options')\n"
    assert sorted(_routes(tmp_path, {"broken.py": src})) == ["legacy", "legacy-options"]
