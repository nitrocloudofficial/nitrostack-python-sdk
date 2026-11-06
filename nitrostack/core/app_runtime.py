"""Application runtime orchestration and compatibility exports."""

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from nitrostack import __version__ as _sdk_version
from nitrostack.core.app_config import (
    ServerConfig,
    _apply_project_identity,
)
from nitrostack.core.app_definitions import DefinitionsMixin
from nitrostack.core.app_entries import _PromptEntry, _ResourceEntry, _ToolEntry
from nitrostack.core.app_invocation import InvocationMixin
from nitrostack.core.app_protocol import ProtocolHandlersMixin
from nitrostack.core.app_registration import RegistrationMixin
from nitrostack.core.app_resources import ResourcesMixin
from nitrostack.core.app_startup import StartupMixin
from nitrostack.core.app_tasks import TaskProtocolMixin
from nitrostack.core.app_tool_results import ToolResultsMixin
from nitrostack.core.decorators import ToolConfig
from nitrostack.core.interceptors.data_spillover import (
    SpilloverRegistry,
)
from nitrostack.core.mcp_server import NitroStackMcpServer
from nitrostack.core.task import TaskManager
from nitrostack.core.visibility import VisibilityTransform
from nitrostack.protocol.version import (
    EraSource,
    ProtocolEra,
    resolve_protocol_era_resolution,
)
from nitrostack.runtime.correlation import InFlightRegistry
from nitrostack.transforms import (
    TransformPipeline,
    TransformRegistry,
)

logger = logging.getLogger(__name__)


class McpApplication(
    RegistrationMixin,
    ProtocolHandlersMixin,
    DefinitionsMixin,
    InvocationMixin,
    ToolResultsMixin,
    ResourcesMixin,
    TaskProtocolMixin,
    StartupMixin,
):
    def __init__(self, app_class: Type):
        self.app_class = app_class
        self._started_at = time.monotonic()
        self._sdk_version = _sdk_version
        if hasattr(app_class, "_mcp_app_module"):
            self.root_module = app_class._mcp_app_module
            self.server_config = app_class._mcp_app_server
        elif hasattr(app_class, "_mcp_module_config"):
            self.root_module = app_class
            self.server_config = ServerConfig(
                name=app_class._mcp_module_config.name or "mcp-server"
            )
        else:
            raise ValueError(
                "Invalid application class. Must be decorated with @mcp_app or @module."
            )
        _apply_project_identity(self.server_config)

        self.mcp_server: Optional[NitroStackMcpServer] = None
        era_resolution = resolve_protocol_era_resolution(
            config_value=self.server_config.protocol_era
        )
        self.protocol_era: ProtocolEra = era_resolution.era
        self.protocol_era_source: EraSource = era_resolution.source

        # nitrostack owns these registries so any number of low-level `Server`
        # instances can be wired against the same tools/resources/prompts (see
        # `create_configured_mcp_server`).
        self._tools: Dict[str, _ToolEntry] = {}
        self._resources: Dict[str, _ResourceEntry] = {}
        self._resource_templates: List[_ResourceEntry] = []
        self._prompts: Dict[str, _PromptEntry] = {}
        self._initial_tools: List[Tuple[Any, Callable, ToolConfig]] = []
        self.task_manager = TaskManager()
        self._in_flight = InFlightRegistry()
        self.visibility_transform = VisibilityTransform()
        self._spillover = SpilloverRegistry()

        # Pipeline first: handlers consult it lazily when requests arrive.
        self._transforms = TransformPipeline(getattr(self.app_class, "_mcp_transforms", None) or [])

        self._bootstrap()

        # Tools are registered now, so transforms can see the catalog.
        self._transforms.register(TransformRegistry(self._base_tool_list))


class McpApplicationFactory:
    @classmethod
    async def create(cls, app_class: Type) -> McpApplication:
        """Bootstraps and instantiates the McpApplication class."""
        return McpApplication(app_class)
