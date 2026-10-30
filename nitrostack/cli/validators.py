"""Project validation for `nitrostack-py validate`."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from nitrostack.cli._shared import (
    EXCLUDE_DIR_NAMES,
    parse_pyproject_dependencies,
    parse_requirements,
    read_text,
    split_requirement,
)


@dataclass
class ValidationIssue:
    severity: str  # "error" or "warning"
    message: str
    hint: str = ""
    path: str = ""

    def format(self) -> str:
        location = f"{self.path}: " if self.path else ""
        text = f"[{self.severity.upper()}] {location}{self.message}"
        if self.hint:
            text += f"\n        → {self.hint}"
        return text


def _iter_python_files(root: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _read(path: str) -> str:
    return read_text(path)


def _parse_pyproject_dependencies(text: str) -> List[str]:
    return parse_pyproject_dependencies(text)


def _parse_requirements(path: str) -> List[str]:
    return parse_requirements(path)


def _split_req(req: str) -> Tuple[str, str]:
    return split_requirement(req)


def _specifier_set(spec: str) -> Optional[SpecifierSet]:
    text = (spec or "").strip()
    if not text:
        return None
    try:
        return SpecifierSet(text)
    except InvalidSpecifier:
        return None


def _exact_version(spec_set: SpecifierSet) -> Optional[Version]:
    items = list(spec_set)
    if len(items) == 1 and items[0].operator in {"==", "==="}:
        try:
            return Version(items[0].version)
        except InvalidVersion:
            return None
    return None


def _constraints_conflict(a: str, b: str) -> bool:
    """Conflict check using PEP 440 specifiers (pre/post/local versions included)."""
    set_a = _specifier_set(a)
    set_b = _specifier_set(b)
    if set_a is None or set_b is None:
        return False
    va, vb = _exact_version(set_a), _exact_version(set_b)
    if va is not None and vb is not None:
        return Version(va.base_version) != Version(vb.base_version)
    if va is not None:
        return not set_b.contains(va, prereleases=True)
    if vb is not None:
        return not set_a.contains(vb, prereleases=True)
    return False


def validate_dependencies(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    pyproject = os.path.join(root, "pyproject.toml")
    requirements = os.path.join(root, "requirements.txt")
    has_pyproject = os.path.isfile(pyproject)
    has_requirements = os.path.isfile(requirements)

    if not has_pyproject and not has_requirements:
        issues.append(ValidationIssue(
            "error",
            "Neither pyproject.toml nor requirements.txt was found.",
            "Create one of these files and declare a `nitrostack` dependency.",
        ))
        return issues

    py_deps = _parse_pyproject_dependencies(_read(pyproject)) if has_pyproject else []
    req_deps = _parse_requirements(requirements) if has_requirements else []

    if has_pyproject and not py_deps:
        issues.append(ValidationIssue(
            "warning",
            "[project].dependencies is missing or empty in pyproject.toml.",
            "Add your runtime packages, including nitrostack, to [project].dependencies.",
            "pyproject.toml",
        ))
    if has_requirements and not req_deps:
        issues.append(ValidationIssue(
            "warning",
            "requirements.txt is empty.",
            "List runtime packages (at least `nitrostack`) so install/pack can reproduce the environment.",
            "requirements.txt",
        ))

    py_map = dict(_split_req(dep) for dep in py_deps)
    req_map = dict(_split_req(dep) for dep in req_deps)

    if has_pyproject and has_requirements:
        for name in sorted(set(py_map) & set(req_map)):
            if _constraints_conflict(py_map[name], req_map[name]):
                issues.append(ValidationIssue(
                    "error",
                    f"Conflicting version specs for '{name}': "
                    f"pyproject.toml has '{name}{py_map[name] or ''}' but "
                    f"requirements.txt has '{name}{req_map[name] or ''}'.",
                    "Make the two files agree on one version range, then re-run validate.",
                ))

    declared = {_split_req(d)[0] for d in py_deps + req_deps}
    project_name = ""
    if has_pyproject:
        match = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', _read(pyproject), re.MULTILINE)
        project_name = (match.group(1) if match else "").lower()
    if "nitrostack" not in declared and project_name != "nitrostack":
        issues.append(ValidationIssue(
            "error",
            "The `nitrostack` package is not declared in pyproject.toml or requirements.txt.",
            "Add `nitrostack` to [project].dependencies (or requirements.txt) so the server can be installed.",
        ))

    return issues


def _decorator_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    elif isinstance(node, ast.Call):
        names.update(_decorator_names(node.func))
    return names


def _module_name_from_path(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    no_ext = os.path.splitext(rel)[0]
    parts = no_ext.replace("\\", "/").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _load_module(path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import spec for '{path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _parse_python_ast(path: str) -> Optional[ast.AST]:
    try:
        return ast.parse(_read(path), filename=path)
    except SyntaxError:
        return None


def _file_has_decorated_class(path: str, decorator: str) -> bool:
    tree = _parse_python_ast(path)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                if decorator in _decorator_names(dec):
                    return True
    return False


def _is_mcp_factory_create(call: ast.Call) -> bool:
    """True for ``McpApplicationFactory.create(...)`` (plain or qualified)."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "create":
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id == "McpApplicationFactory"
    if isinstance(value, ast.Attribute):
        return value.attr == "McpApplicationFactory"
    return False


def _factory_create_arg_name(call: ast.Call) -> Optional[str]:
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def _has_module_factory_bootstrap(root: str) -> bool:
    """True when some ``McpApplicationFactory.create(X)`` targets an ``@module`` class ``X``.

    Mirrors ``McpApplication.__init__``'s ``_mcp_module_config`` path used by init templates.
    """
    module_class_names: Set[str] = set()
    create_targets: Set[str] = set()
    for path in _iter_python_files(root):
        tree = _parse_python_ast(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for dec in node.decorator_list:
                    if "module" in _decorator_names(dec):
                        module_class_names.add(node.name)
                        break
            elif isinstance(node, ast.Call) and _is_mcp_factory_create(node):
                name = _factory_create_arg_name(node)
                if name:
                    create_targets.add(name)
    return bool(module_class_names & create_targets)


def validate_mcp_app_imports(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    found_mcp_app = False
    for path in _iter_python_files(root):
        if not _file_has_decorated_class(path, "mcp_app"):
            continue
        found_mcp_app = True
        rel = os.path.relpath(path, root)
        module_name = f"nitrostack_validate_{_module_name_from_path(root, path).replace('.', '_')}"
        try:
            module = _load_module(path, module_name)
        except Exception as exc:
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            issues.append(ValidationIssue(
                "error",
                f"Failed to import @mcp_app module '{rel}': {tb}",
                "Fix the import/name error in this file (missing package, typo, or circular import) "
                "and re-run `nitrostack-py validate`.",
                rel,
            ))
            continue
        app_classes = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and hasattr(obj, "_mcp_app_module")
        ]
        if not app_classes:
            issues.append(ValidationIssue(
                "error",
                f"'{rel}' uses @mcp_app but no decorated application class was found after import.",
                "Ensure the @mcp_app decorator is applied to a class in this file.",
                rel,
            ))
            continue
        for cls in app_classes:
            app_module = getattr(cls, "_mcp_app_module", None)
            if app_module is None:
                issues.append(ValidationIssue(
                    "error",
                    f"{cls.__name__} is decorated with @mcp_app but has no root module.",
                    "Pass a real module class to @mcp_app(module=..., server=...).",
                    rel,
                ))
            elif not isinstance(app_module, type):
                issues.append(ValidationIssue(
                    "error",
                    f"{cls.__name__} @mcp_app(module=...) does not reference a class "
                    f"(got {type(app_module).__name__}).",
                    "Pass the AppModule class itself, not an instance or string.",
                    rel,
                ))
    if not found_mcp_app and not _has_module_factory_bootstrap(root):
        issues.append(ValidationIssue(
            "warning",
            "No @mcp_app- or @module-decorated application class was found in this project.",
            "If this is an MCP server, decorate a root class with "
            "@mcp_app(module=AppModule, server=ServerConfig(...)) "
            "or pass a @module-decorated class to McpApplicationFactory.create().",
        ))
    return issues


def _is_real_class(value: Any) -> bool:
    return isinstance(value, type)


def validate_module_references(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for path in _iter_python_files(root):
        if not _file_has_decorated_class(path, "module"):
            continue
        rel = os.path.relpath(path, root)
        module_name = f"nitrostack_validate_mod_{_module_name_from_path(root, path).replace('.', '_')}"
        try:
            module = _load_module(path, module_name)
        except Exception as exc:
            tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            issues.append(ValidationIssue(
                "error",
                f"Failed to import @module file '{rel}': {tb}",
                "Resolve the import error (typo, missing file, or broken relative import) and retry.",
                rel,
            ))
            continue

        for obj in vars(module).values():
            if not isinstance(obj, type) or not hasattr(obj, "_mcp_module_config"):
                continue
            config = getattr(obj, "_mcp_module_config")
            for field in ("imports", "exports", "controllers", "providers"):
                entries = getattr(config, field, []) or []
                for index, entry in enumerate(entries):
                    if not _is_real_class(entry):
                        issues.append(ValidationIssue(
                            "error",
                            f"{obj.__name__}.{field}[{index}] is {entry!r} "
                            f"({type(entry).__name__}), not a class.",
                            f"Use the class object (e.g. {field[:-1].rstrip('e').title()}Class), "
                            "not a string or instance. Check for typos in the @module() lists.",
                            rel,
                        ))
                        continue
                    if field == "imports" and not hasattr(entry, "_mcp_module_config"):
                        issues.append(ValidationIssue(
                            "error",
                            f"{obj.__name__}.imports[{index}] references {entry.__name__}, "
                            "which is not decorated with @module().",
                            "Import a real NitroStack module class, or remove this entry from imports=[].",
                            rel,
                        ))
    return issues


_WIDGET_DECORATOR_RE = re.compile(r"""@widget\(\s*["']([^"']+)["']""")
_WIDGET_OPTIONS_RE = re.compile(r"""WidgetOptions\(\s*route\s*=\s*["']([^"']+)["']""")
_WIDGET_ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")



def _collect_widget_routes(root: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    root_abs = os.path.abspath(root)

    for path in sorted(_iter_python_files(root)):
        rel_path = os.path.relpath(path, root_abs)
        parts = os.path.normpath(rel_path).split(os.sep)

        # Ignore a vendored copy of the NitroStack SDK.
        if "vendor" in parts and "nitrostack" in parts:
            continue

        try:
            text = _read(path)
        except OSError:
            continue

        for route in (
            _WIDGET_DECORATOR_RE.findall(text)
            + _WIDGET_OPTIONS_RE.findall(text)
        ):
            if route not in seen:
                seen.add(route)
                found.append((route, rel_path))

    return found



def validate_widgets(root: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for route, source in _collect_widget_routes(root):
        if not _WIDGET_ROUTE_RE.fullmatch(route):
            issues.append(ValidationIssue(
                "warning",
                f"@widget route {route!r} is not a single alphanumeric path segment",
                "Use letters, digits, '-' or '_' only; the route maps to widgets/out/<route>.html.",
                source,
            ))
            continue
        rel = os.path.join("widgets", "out", f"{route}.html")
        uri = f"ui://widget/{route}.html"
        html_path = os.path.join(root, rel)
        if not os.path.isfile(html_path):
            issues.append(ValidationIssue(
                "warning",
                f"widget route {route!r} -> {rel} -> {uri}: file is missing",
                f"Create {rel} with the widget HTML.",
                source,
            ))
        elif not _read(html_path).strip():
            issues.append(ValidationIssue(
                "warning",
                f"widget route {route!r} -> {rel} -> {uri}: file is empty",
                f"Add widget HTML to {rel}.",
                rel,
            ))
    return issues

def validate_project(root: Optional[str] = None) -> List[ValidationIssue]:
    root = os.path.abspath(root or os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    issues: List[ValidationIssue] = []
    issues.extend(validate_dependencies(root))
    issues.extend(validate_mcp_app_imports(root))
    issues.extend(validate_module_references(root))
    issues.extend(validate_widgets(root))
    return issues


def format_report(issues: Sequence[ValidationIssue]) -> str:
    if not issues:
        return "Validation passed. No issues found."
    lines = ["Validation found the following issues:", ""]
    for issue in issues:
        lines.append(issue.format())
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    lines.append("")
    lines.append(f"{errors} error(s), {warnings} warning(s).")
    return "\n".join(lines)
