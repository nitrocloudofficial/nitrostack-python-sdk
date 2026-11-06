import shutil
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


def test_helper_call_with_other_positional_arguments_is_not_guessed(tmp_path: Path):
    src = "@widget(helper('not-a-route', True))\ndef f(): ...\n"
    assert validate_widgets(str(_project(tmp_path, src))) == []


def test_invalid_helper_route_is_reported(tmp_path: Path):
    issues = validate_widgets(str(_project(tmp_path, "@widget(helper('../evil'))\ndef f(): ...\n")))
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
