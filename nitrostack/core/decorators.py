from dataclasses import dataclass, field
from typing import Any, Callable, List, Dict, Optional, Type, Literal, Union
from functools import wraps

from nitrostack.widgets.component import WidgetOptions, parse_widget_options

@dataclass
class ToolAnnotations:
    destructive_hint: bool = True
    idempotent_hint: bool = False
    read_only_hint: bool = False
    open_world_hint: bool = True

@dataclass
class ResourceAnnotations:
    audience: List[Literal["user", "assistant"]] = field(default_factory=list)
    priority: float = 1.0
    last_modified: Optional[str] = None

@dataclass
class PromptArgument:
    name: str
    description: str
    required: bool = False

@dataclass
class PromptMessage:
    role: Literal["user", "assistant", "system"]
    content: str

@dataclass
class ToolInvocation:
    invoking: str
    invoked: str

@dataclass
class ToolExamples:
    input: Any
    output: Any
    description: Optional[str] = None

@dataclass
class ToolConfig:
    name: str
    description: str
    input_schema: Any  # Pydantic model class or dict schema
    title: Optional[str] = None
    output_schema: Optional[Any] = None
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)
    task_support: Literal["forbidden", "optional", "required"] = "forbidden"
    visibility: Literal["visible", "hidden"] = "visible"
    examples: Optional[ToolExamples] = None
    invocation: Optional[ToolInvocation] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_initial: bool = False

@dataclass
class ResourceConfig:
    uri: str
    name: str
    description: str
    title: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    annotations: ResourceAnnotations = field(default_factory=ResourceAnnotations)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PromptConfig:
    name: str
    description: str
    arguments: List[PromptArgument] = field(default_factory=list)


def widget_resource_uri(route_path: str) -> str:
    """Normalize a widget route to the MCP Apps `ui://` resource URI.

    MCP Inspector throws (and blanks the Tools tab) if `_meta.ui.resourceUri`
    is set to anything that does not start with `ui://`.
    """
    route = (route_path or "").strip()
    if not route:
        raise ValueError("widget route must not be empty")
    if route.startswith("ui://"):
        return route
    name = route.strip("/").removeprefix("widget/").removesuffix(".html").strip("/")
    if not name:
        raise ValueError("widget route must not be empty")
    return f"ui://widget/{name}.html"


def tool(
    name: str,
    description: str,
    input_schema: Any,
    title: Optional[str] = None,
    output_schema: Optional[Any] = None,
    annotations: Optional[ToolAnnotations] = None,
    task_support: Literal["forbidden", "optional", "required"] = "forbidden",
    visibility: Literal["visible", "hidden"] = "visible",
    examples: Optional[ToolExamples] = None,
    invocation: Optional[ToolInvocation] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Decorator to register a method as an MCP Tool.
    """
    if annotations is None:
        annotations = ToolAnnotations()
    if metadata is None:
        metadata = {}

    def decorator(func: Callable):
        # Attach or update tool config
        config = ToolConfig(
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            annotations=annotations,
            task_support=task_support,
            visibility=visibility,
            examples=examples,
            invocation=invocation,
            metadata=metadata,
            is_initial=getattr(func, "_mcp_is_initial", False)
        )
        func._mcp_tool_config = config
        return func
    return decorator

def widget(route_or_options: Union[str, WidgetOptions, Dict[str, Any]]):
    """
    Decorator to associate a UI widget route with a tool.

    Accepts a route string, :class:`WidgetOptions`, or a snake_case dict.
    """
    parse_widget_options(route_or_options)

    def decorator(func: Callable):
        func._mcp_widget = route_or_options
        return func
    return decorator

def initial_tool(func: Callable):
    """
    Stacked decorator with @tool to mark it as auto-called on client connection.
    Can be placed before or after @tool.
    """
    func._mcp_is_initial = True
    if hasattr(func, "_mcp_tool_config"):
        func._mcp_tool_config.is_initial = True
    return func

def resource(
    uri: str,
    name: str,
    description: str,
    title: Optional[str] = None,
    mime_type: Optional[str] = None,
    size: Optional[int] = None,
    annotations: Optional[ResourceAnnotations] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Decorator to register a method as an MCP Resource.
    """
    if annotations is None:
        annotations = ResourceAnnotations()
    if metadata is None:
        metadata = {}

    def decorator(func: Callable):
        config = ResourceConfig(
            uri=uri,
            name=name,
            description=description,
            title=title,
            mime_type=mime_type,
            size=size,
            annotations=annotations,
            metadata=metadata
        )
        func._mcp_resource_config = config
        return func
    return decorator

def prompt(
    name: str,
    description: str,
    arguments: Optional[List[PromptArgument]] = None,
):
    """
    Decorator to register a method as an MCP Prompt template.
    """
    if arguments is None:
        arguments = []

    def decorator(func: Callable):
        config = PromptConfig(
            name=name,
            description=description,
            arguments=arguments
        )
        func._mcp_prompt_config = config
        return func
    return decorator
