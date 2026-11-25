"""Registration implementation mixin."""

import datetime
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Set, Type
from unittest.mock import NonCallableMock

import mcp.types as types

from nitrostack.core.additional_decorators import HealthCheckRegistry, ServerHealthPayload
from nitrostack.core.app_entries import _PromptEntry, _ResourceEntry, _ToolEntry
from nitrostack.core.app_mode import get_widget_mime_type
from nitrostack.core.app_schema import get_pydantic_model
from nitrostack.core.context import ExecutionContext
from nitrostack.core.decorators import PromptConfig, ResourceConfig, ToolConfig, widget_resource_uri
from nitrostack.core.di import DIContainer
from nitrostack.core.errors import (
    DependencyResolutionError,
)
from nitrostack.core.interceptors.data_spillover import (
    DataSpilloverInterceptor,
)
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.events.event_emitter import EventEmitter
from nitrostack.protocol.resources import extract_template_param_names, uri_template_to_pattern
from nitrostack.protocol.schema import (
    gate_registered_schema,
)
from nitrostack.widgets.component import (
    Component,
    find_project_root,
    load_widget_html,
    parse_widget_options,
)
from nitrostack.widgets.registry import WIDGET_EXAMPLES_URI, WidgetRegistry
from nitrostack.widgets.route_templates import build_missing_widget

logger = logging.getLogger(__name__)


class RegistrationMixin:
    def _base_tool_list(self) -> List[types.Tool]:
        return [self._build_tool_definition(entry) for entry in self._tools.values()]

    def _bootstrap(self) -> None:
        # 1. Construct the low-level server directly.
        self.mcp_server = NitroStackMcpServer(
            name=self.server_config.name,
            version=self.server_config.version,
        )

        # 2. Resolve Module Tree recursively and register services
        resolved_modules: Set[Type] = set()
        self._resolve_modules(self.root_module, resolved_modules)

        container = DIContainer.get_instance()
        self._assert_declared_dependencies(resolved_modules, container)

        # Instantiate all providers and controllers to populate container.
        # Scan both: providers with @tool/@resource/@prompt must stay discoverable.
        module_instances: List[Any] = []
        seen_instance_ids: Set[int] = set()
        for mod in resolved_modules:
            mod_config = getattr(mod, "_mcp_module_config", None)
            if mod_config:
                for provider in mod_config.providers:
                    instance = container.resolve(provider)
                    if id(instance) not in seen_instance_ids:
                        seen_instance_ids.add(id(instance))
                        module_instances.append(instance)
                for controller in mod_config.controllers:
                    instance = container.resolve(controller)
                    if id(instance) not in seen_instance_ids:
                        seen_instance_ids.add(id(instance))
                        module_instances.append(instance)

        # 3. Discover decorated methods on resolved module providers and controllers
        for instance in module_instances:
            # Scan members of this instance
            for _name, member in inspect.getmembers(instance):
                # A test mock answers hasattr() for everything, so it would look like a tool.
                if isinstance(member, NonCallableMock):
                    continue
                # Discover Tools
                if hasattr(member, "_mcp_tool_config"):
                    tool_config: ToolConfig = member._mcp_tool_config
                    self._register_tool(instance, member, tool_config)

                # Discover Resources
                if hasattr(member, "_mcp_resource_config"):
                    resource_config: ResourceConfig = member._mcp_resource_config
                    self._register_resource(instance, member, resource_config)

                # Discover Prompts
                if hasattr(member, "_mcp_prompt_config"):
                    prompt_config: PromptConfig = member._mcp_prompt_config
                    self._register_prompt(instance, member, prompt_config)

                # Bind Health Checks
                if hasattr(member, "_mcp_health_check_name"):
                    check_name = member._mcp_health_check_name
                    HealthCheckRegistry.bind_instance(
                        check_name,
                        member,
                        instance,
                        timeout=float(getattr(member, "_mcp_health_check_timeout", 5.0)),
                    )

                # Bind Event Listeners
                if hasattr(member, "_mcp_event_name"):
                    event_name = member._mcp_event_name
                    EventEmitter.get_instance().bind_instance(event_name, member, instance)

        self.widget_registry: WidgetRegistry = WidgetRegistry._from_tools(self._tools.values())
        if self.widget_registry.get_all_widgets():
            self._register_widget_examples_resource()

        # 4. Register Built-in Health check Resource if any checks exist
        if HealthCheckRegistry.get_checks():
            self._register_health_resource()

        # 5. Wire every protocol handler (tools/resources/prompts/tasks/notifications)
        #    onto the owned low-level server. This is factored out so additional server
        #    instances (e.g. one per HTTP session) can be configured identically.
        for entry in self._tools.values():
            for candidate in getattr(entry.method, "_mcp_interceptors", []):
                interceptor = (
                    container.resolve(candidate) if isinstance(candidate, type) else candidate
                )
                if isinstance(interceptor, DataSpilloverInterceptor):
                    self._spillover.register(interceptor)
        self._setup_handlers(self.mcp_server)

    def _assert_declared_dependencies(
        self, resolved_modules: Set[Type], container: DIContainer
    ) -> None:
        """Fail at bootstrap when a string ``deps=[...]`` token was never registered."""
        missing: List[str] = []
        seen: Set[Type] = set()

        def walk(cls: Any) -> None:
            if not isinstance(cls, type) or cls in seen:
                return
            seen.add(cls)
            for dep in getattr(cls, "_mcp_deps", []) or []:
                if isinstance(dep, str):
                    if not container.has_value(dep) and dep not in container._registry:
                        missing.append(f"{cls.__name__} -> '{dep}'")
                elif isinstance(dep, type):
                    walk(dep)

        for mod in resolved_modules:
            mod_config = getattr(mod, "_mcp_module_config", None)
            if not mod_config:
                continue
            for cls in [*mod_config.providers, *mod_config.controllers]:
                walk(cls)

        if missing:
            raise DependencyResolutionError(
                "Missing dependency at app bootstrap (referenced in deps=[...] "
                "but never registered): " + "; ".join(missing)
            )

    def _resolve_modules(self, module_class: Type, resolved_modules: Set[Type]) -> None:
        if module_class in resolved_modules:
            return
        resolved_modules.add(module_class)

        mod_config = getattr(module_class, "_mcp_module_config", None)
        if mod_config:
            for imp in mod_config.imports:
                self._resolve_modules(imp, resolved_modules)

    def _register_tool(self, instance: Any, method: Callable, tool_config: ToolConfig) -> None:
        gate_registered_schema(tool_config.input_schema, name=f"tool {tool_config.name!r} input")
        gate_registered_schema(tool_config.output_schema, name=f"tool {tool_config.name!r} output")
        input_model = get_pydantic_model(tool_config.input_schema)
        entry = _ToolEntry(
            config=tool_config, input_model=input_model, instance=instance, method=method
        )

        widget_spec = getattr(method, "_mcp_widget", None)
        if widget_spec is not None:
            options = parse_widget_options(widget_spec)
            resource_uri = widget_resource_uri(options.route)
            route_id = resource_uri.removeprefix("ui://widget/").removesuffix(".html")
            from_file = None
            method_module = inspect.getmodule(method)
            if method_module and getattr(method_module, "__file__", None):
                from_file = Path(method_module.__file__).resolve()
            project_root = find_project_root(from_file)
            html = load_widget_html(
                route_id,
                html=options.html,
                from_file=from_file,
                project_root=project_root,
            )
            if not html:
                html = build_missing_widget(route_id)
            component = Component(
                id=route_id,
                name=tool_config.title or tool_config.name,
                html=html,
                description=options.description or tool_config.description,
                css=options.css,
                js=options.js,
                csp=options.csp,
                domain=options.domain,
                prefers_border=options.prefers_border,
                can_invoke_tools=options.can_invoke_tools,
            )
            entry.component = component
            self._register_widget_resource(component)

        self._tools[tool_config.name] = entry
        if tool_config.is_initial:
            self._initial_tools.append((instance, method, tool_config))

    def _register_widget_resource(self, component: Component) -> None:
        config = ResourceConfig(
            uri=component.resource_uri,
            name=component.name,
            description=component.description or f"UI widget for {component.name}",
            mime_type=get_widget_mime_type(),
        )

        async def widget_resource_handler(context: ExecutionContext) -> str:
            return component.get_bundle()

        self._resources[config.uri] = _ResourceEntry(
            config=config,
            instance=None,
            method=widget_resource_handler,
            component=component,
        )

    def _register_widget_examples_resource(self) -> None:
        if WIDGET_EXAMPLES_URI in self._resources:
            raise ValueError(
                f"Resource URI {WIDGET_EXAMPLES_URI!r} is reserved for the widget manifest"
            )

        async def widget_examples(context: ExecutionContext) -> dict:
            return self.widget_registry.get_manifest().to_dict()

        self._resources[WIDGET_EXAMPLES_URI] = _ResourceEntry(
            config=ResourceConfig(
                uri=WIDGET_EXAMPLES_URI,
                name="Widget Examples",
                description="Provides metadata and examples for all registered UI widgets",
                mime_type="application/json",
            ),
            instance=None,
            method=widget_examples,
        )

    def _register_resource(
        self, instance: Any, method: Callable, resource_config: ResourceConfig
    ) -> None:
        gate_registered_schema(
            getattr(resource_config, "schema", None)
            or (resource_config.metadata or {}).get("schema"),
            name=f"resource {resource_config.uri!r}",
        )
        param_names = extract_template_param_names(resource_config.uri)
        entry = _ResourceEntry(
            config=resource_config, instance=instance, method=method, param_names=param_names
        )

        if param_names:
            entry.pattern = uri_template_to_pattern(resource_config.uri)
            self._resource_templates.append(entry)
        else:
            self._resources[resource_config.uri] = entry

    def _register_prompt(
        self, instance: Any, method: Callable, prompt_config: PromptConfig
    ) -> None:
        for argument in prompt_config.arguments or []:
            gate_registered_schema(
                getattr(argument, "schema", None),
                name=f"prompt {prompt_config.name!r} argument {argument.name!r}",
            )
        self._prompts[prompt_config.name] = _PromptEntry(
            config=prompt_config, instance=instance, method=method
        )

    def _register_health_resource(self) -> None:
        status_config = ResourceConfig(
            uri="health://status",
            name="Health Status",
            description="System health status check",
            mime_type="application/json",
        )
        checks_config = ResourceConfig(
            uri="health://checks",
            name="Health Checks & Pipeline Telemetry",
            description="Current health status and transform pipeline telemetry",
            mime_type="application/json",
        )

        async def health_status_resource(context: ExecutionContext) -> str:
            # Keep the original string map at health://status for existing clients.
            results = await HealthCheckRegistry.run_all()
            return json.dumps(results)

        async def health_checks_resource(context: ExecutionContext) -> ServerHealthPayload:
            checks = await HealthCheckRegistry.run_detailed()
            has_failures = any(check["status"] in {"error", "timeout"} for check in checks)
            has_unhealthy = any(check["status"] == "unhealthy" for check in checks)
            transforms = []
            configured_transforms = getattr(self.app_class, "_mcp_transforms", None) or []
            for order, transform in enumerate(configured_transforms):
                transforms.append(
                    {
                        "name": getattr(transform, "name", transform.__class__.__name__),
                        "type": transform.__class__.__name__,
                        "order": order,
                        "details": {},
                    }
                )
            public_checks = [
                {
                    **check,
                    "message": "Check failed" if check["status"] == "error" else check["message"],
                }
                for check in checks
            ]
            payload: ServerHealthPayload = {
                "status": (
                    "unhealthy" if has_failures else "degraded" if has_unhealthy else "healthy"
                ),
                "uptimeSeconds": int(time.monotonic() - self._started_at),
                "checks": public_checks,
                "count": len(public_checks),
                "transforms": transforms,
                "timestamp": datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "sdkVersion": self._sdk_version,
            }
            return payload

        self._resources[status_config.uri] = _ResourceEntry(
            config=status_config, instance=None, method=health_status_resource
        )
        self._resources[checks_config.uri] = _ResourceEntry(
            config=checks_config, instance=None, method=health_checks_resource
        )
