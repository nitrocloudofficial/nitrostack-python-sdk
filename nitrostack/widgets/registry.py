"""Application-owned descriptions of bootstrapped widgets, without their HTML."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from nitrostack.core.app_mode import get_widget_mime_type
from nitrostack.widgets.mcp_meta import resource_read_contents_meta

if TYPE_CHECKING:
    from nitrostack.core.app import McpApplication


WIDGET_EXAMPLES_URI = "widget://examples"


@dataclass(frozen=True)
class WidgetExample:
    tool_name: str
    input: Any
    output: Any
    description: str | None = None


@dataclass(frozen=True)
class WidgetMetadata:
    route: str
    uri: str
    name: str
    description: str | None
    mime_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    examples: list[WidgetExample] = field(default_factory=list)


@dataclass(frozen=True)
class WidgetManifest:
    widgets: list[WidgetMetadata]
    count: int
    loaded: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, serializable manifest, including declared examples."""
        return asdict(self)


class WidgetRegistry:
    """A startup snapshot; all reads return copies owned by the caller."""

    def __init__(self, widgets: Iterable[WidgetMetadata] = ()) -> None:
        self._widgets = {
            widget.uri: deepcopy(widget)
            for widget in sorted(widgets, key=lambda widget: widget.route)
        }

    @classmethod
    def _from_tools(cls, tools: Iterable[Any]) -> WidgetRegistry:
        # Work with final app registrations, after Python has evaluated helpers.
        groups: dict[str, list[Any]] = {}
        for entry in tools:
            if entry.component is not None:
                groups.setdefault(entry.component.id, []).append(entry)

        widgets = []
        for route, entries in sorted(groups.items()):
            entries.sort(key=lambda entry: entry.config.name)
            component = entries[0].component
            examples = [
                WidgetExample(
                    tool_name=entry.config.name,
                    input=entry.config.examples.input,
                    output=entry.config.examples.output,
                    description=entry.config.examples.description,
                )
                for entry in entries
                if entry.config.examples is not None
            ]
            widgets.append(WidgetMetadata(
                route=route,
                uri=component.resource_uri,
                name=component.name,
                description=component.description,
                mime_type=get_widget_mime_type(),
                metadata=resource_read_contents_meta(component) or {},
                tool_names=[entry.config.name for entry in entries],
                examples=examples,
            ))
        return cls(widgets)

    def get_all_widgets(self) -> list[WidgetMetadata]:
        return deepcopy(list(self._widgets.values()))

    def get_widget(self, uri: str) -> WidgetMetadata | None:
        return deepcopy(self._widgets.get(uri))

    def get_manifest(self) -> WidgetManifest:
        widgets = self.get_all_widgets()
        return WidgetManifest(widgets=widgets, count=len(widgets), loaded=True)


def get_widget_registry(app: McpApplication) -> WidgetRegistry:
    """Return this application's registry; no global catalog is maintained."""
    return app.widget_registry
