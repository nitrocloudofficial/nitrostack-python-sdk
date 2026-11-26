"""Static discovery of ``@widget`` routes for ``nitrostack-py validate``.

Nothing is imported or run. Each project file is parsed and routes are found in three
ways: a string literal (``@widget("card")``), a ``WidgetOptions`` call with a literal
route, and a call to a *factory*, meaning a function whose parameter reaches
``WidgetOptions(route=...)`` (``@widget(my_widget("pizza-map"))``).

``widget`` and ``WidgetOptions`` only count when they resolve to the SDK's own, directly,
through ``import nitrostack``, or through a project module that re-exports them, so
``@pytest.mark.widget("smoke")`` is ignored.

A factory call only counts when the name resolves, through the calling module's own
definitions and imports, to a real factory, and the string sits in the argument that
feeds ``route``. So ``panel("x")`` is ignored when ``panel`` never passes that argument
to ``route``, or when it is an unrelated ``panel`` from another module.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# Only used for a file that does not parse, so a broken file still gets its literal routes.
_LEGACY_DECORATOR_RE = re.compile(r"""@widget\(\s*["']([^"']+)["']""")
_LEGACY_OPTIONS_RE = re.compile(r"""WidgetOptions\(\s*route\s*=\s*["']([^"']+)["']""")

_WIDGET_OPTIONS = object()  # the SDK's WidgetOptions class
_WIDGET = object()          # the SDK's @widget decorator
_NITROSTACK = object()      # the nitrostack package or one of its submodules
_PLAIN = object()           # a project function or class that is not a factory


@dataclass(frozen=True)
class _Factory:
    param: str  # the parameter that feeds WidgetOptions.route
    index: int  # its position among the positional parameters, -1 if keyword-only


@dataclass
class _Namespace:
    """A project module or a class, with the names it defines."""

    name: str
    defs: Dict[str, object] = field(default_factory=dict)


@dataclass
class _Module:
    name: str
    package: Tuple[str, ...]
    tree: Optional[ast.Module]
    text: str
    ns: _Namespace
    imports: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # local -> (module, attr)
    funcs: List[Tuple[ast.AST, str, _Namespace]] = field(default_factory=list)  # node, kind, owner


def _dotted(rel_path: str) -> Tuple[str, Tuple[str, ...]]:
    parts = os.path.normpath(rel_path).split(os.sep)
    parts[-1] = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), tuple(parts if is_package else parts[:-1])


def _is_nitrostack(module: str) -> bool:
    return module == "nitrostack" or module.startswith("nitrostack.")


class _Project:
    def __init__(self, sources: Iterable[Tuple[str, str]]):
        self.modules: Dict[str, _Module] = {}
        self.order: List[Tuple[str, _Module]] = []
        for rel_path, text in sources:
            name, package = _dotted(rel_path)
            try:
                tree: Optional[ast.Module] = ast.parse(text)
            except (SyntaxError, ValueError):
                tree = None
            module = _Module(name, package, tree, text, _Namespace(name))
            self.order.append((rel_path, module))
            self.modules[name] = module
            if tree is not None:
                self._index(module)
        self._find_factories()

    # -- indexing ---------------------------------------------------------------

    def _index(self, module: _Module) -> None:
        assert module.tree is not None
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        module.imports[alias.asname] = (alias.name, "")
                    else:
                        top = alias.name.split(".")[0]
                        module.imports[top] = (top, "")
            elif isinstance(node, ast.ImportFrom):
                source = self._absolute(module, node)
                if source is None:
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        module.imports[alias.asname or alias.name] = (source, alias.name)
        for node in module.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.ns.defs[node.name] = _PLAIN
                module.funcs.append((node, "func", module.ns))
            elif isinstance(node, ast.ClassDef):
                cls = _Namespace(f"{module.name}.{node.name}")
                module.ns.defs[node.name] = cls
                for item in node.body:
                    kind = self._method_kind(item)
                    if kind:
                        cls.defs[item.name] = _PLAIN
                        module.funcs.append((item, kind, cls))

    @staticmethod
    def _method_kind(item: ast.AST) -> Optional[str]:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        names = {d.id for d in item.decorator_list if isinstance(d, ast.Name)}
        if "staticmethod" in names:
            return "static"
        return "class" if "classmethod" in names else None  # instance methods need an object

    @staticmethod
    def _absolute(module: _Module, node: ast.ImportFrom) -> Optional[str]:
        if not node.level:
            return node.module
        keep = len(module.package) - (node.level - 1)
        if keep < 0:
            return None
        base = list(module.package[:keep]) + ([node.module] if node.module else [])
        return ".".join(base)

    # -- name resolution --------------------------------------------------------

    def _find(self, dotted: str) -> Optional[_Module]:
        if dotted in self.modules:
            return self.modules[dotted]
        matches = [m for key, m in self.modules.items() if key.endswith("." + dotted)]
        return matches[0] if len(matches) == 1 else None  # e.g. a src/ layout

    @staticmethod
    def _sdk(attr: str) -> object:
        if attr == "WidgetOptions":
            return _WIDGET_OPTIONS
        return _WIDGET if attr == "widget" else _NITROSTACK

    def _binding(self, module: _Module, name: str, seen: set) -> object:
        """What ``name`` means inside ``module``, following re-exports through project modules."""
        if (module.name, name) in seen:  # an import cycle
            return None
        seen.add((module.name, name))
        if name in module.ns.defs:
            return module.ns.defs[name]
        imported = module.imports.get(name)
        if not imported:
            return None
        source, attr = imported
        if _is_nitrostack(source):
            return self._sdk(attr)
        found = self._find(source)
        if not attr:
            return found.ns if found else None
        if found:
            bound = self._binding(found, attr, seen)
            if bound is not None:
                return bound
        sub = self._find(f"{source}.{attr}")  # ``from pkg import module``
        return sub.ns if sub else None

    def _name(self, module: _Module, name: str) -> object:
        bound = self._binding(module, name, set())
        if bound is None and name not in module.ns.defs and name not in module.imports:
            return self._sdk(name) if name in ("WidgetOptions", "widget") else None
        return bound

    def resolve(self, expr: ast.AST, module: _Module) -> object:
        if isinstance(expr, ast.Name):
            return self._name(module, expr.id)
        if isinstance(expr, ast.Attribute):
            base = self.resolve(expr.value, module)
            if base is _NITROSTACK:
                return self._sdk(expr.attr)
            if isinstance(base, _Namespace):
                if expr.attr in base.defs:
                    return base.defs[expr.attr]
                sub = self._find(f"{base.name}.{expr.attr}")
                return sub.ns if sub else None
        return None

    @staticmethod
    def route_arg(call: ast.Call, target: object) -> Optional[ast.AST]:
        """The expression passed where ``target`` expects the route, if it is passed at all."""
        if target is _WIDGET_OPTIONS:
            name, index = "route", 0
        elif isinstance(target, _Factory):
            name, index = target.param, target.index
        else:
            return None
        if 0 <= index < len(call.args):
            if any(isinstance(arg, ast.Starred) for arg in call.args[: index + 1]):
                return None
            return call.args[index]
        for keyword in call.keywords:
            if keyword.arg == name:
                return keyword.value
        return None

    # -- factories --------------------------------------------------------------

    def _factory_of(self, module: _Module, fn: ast.AST, kind: str) -> Optional[_Factory]:
        positional = [a.arg for a in fn.args.posonlyargs + fn.args.args]  # type: ignore[attr-defined]
        if kind == "class":
            positional = positional[1:]
        params = positional + [a.arg for a in fn.args.kwonlyargs]  # type: ignore[attr-defined]
        reassigned = {
            n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
        }
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            expr = self.route_arg(call, self.resolve(call.func, module))
            if isinstance(expr, ast.Name) and expr.id in params and expr.id not in reassigned:
                return _Factory(expr.id, positional.index(expr.id) if expr.id in positional else -1)
        return None

    def _find_factories(self) -> None:
        # Repeat so a function that forwards its parameter to another factory is found too.
        for _ in range(sum(len(m.funcs) for _, m in self.order) + 1):
            changed = False
            for _, module in self.order:
                for fn, kind, owner in module.funcs:
                    if owner.defs.get(fn.name) is not _PLAIN:  # type: ignore[attr-defined]
                        continue
                    factory = self._factory_of(module, fn, kind)
                    if factory:
                        owner.defs[fn.name] = factory  # type: ignore[attr-defined]
                        changed = True
            if not changed:
                break

    # -- routes -----------------------------------------------------------------

    def _literal(self, expr: Optional[ast.AST]) -> Optional[str]:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        return None

    def routes_in(self, module: _Module) -> List[str]:
        if module.tree is None:
            return _LEGACY_DECORATOR_RE.findall(module.text) + _LEGACY_OPTIONS_RE.findall(module.text)
        found: List[Tuple[int, int, str]] = []
        for node in ast.walk(module.tree):
            for decorator in getattr(node, "decorator_list", []):
                is_widget = (
                    isinstance(decorator, ast.Call)
                    and self.resolve(decorator.func, module) is _WIDGET
                )
                if isinstance(decorator, ast.Call) and is_widget and decorator.args:
                    route = self._decorator_route(decorator.args[0], module)
                    if route:
                        found.append((decorator.lineno, decorator.col_offset, route))
            if isinstance(node, ast.Call) and self.resolve(node.func, module) is _WIDGET_OPTIONS:
                route = self._literal(self.route_arg(node, _WIDGET_OPTIONS))
                if route:
                    found.append((node.lineno, node.col_offset, route))
        return [route for _, _, route in sorted(found)]

    def _decorator_route(self, expr: ast.AST, module: _Module) -> Optional[str]:
        literal = self._literal(expr)
        if literal is not None:
            return literal
        if isinstance(expr, ast.Call):
            return self._literal(self.route_arg(expr, self.resolve(expr.func, module)))
        return None


def collect_widget_routes(sources: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """``(route, rel_path)`` for every route found in ``(rel_path, source_text)`` pairs."""
    project = _Project(sources)
    found: List[Tuple[str, str]] = []
    seen = set()
    for rel_path, module in project.order:
        for route in project.routes_in(module):
            if route not in seen:
                seen.add(route)
                found.append((route, rel_path))
    return found
