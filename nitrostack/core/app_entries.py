"""Application registration entries shared by implementation modules."""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Pattern, Type

from pydantic import BaseModel

from nitrostack.core.decorators import PromptConfig, ResourceConfig, ToolConfig
from nitrostack.widgets.component import Component

logger = logging.getLogger(__name__)


@dataclass
class _ToolEntry:
    config: ToolConfig
    input_model: Type[BaseModel]
    instance: Any
    method: Callable
    component: Optional[Component] = None


@dataclass
class _ResourceEntry:
    config: ResourceConfig
    instance: Any
    method: Callable
    param_names: List[str] = field(default_factory=list)
    pattern: Optional[Pattern] = None
    component: Optional[Component] = None


@dataclass
class _PromptEntry:
    config: PromptConfig
    instance: Any
    method: Callable
