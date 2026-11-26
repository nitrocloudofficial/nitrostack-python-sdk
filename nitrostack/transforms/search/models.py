from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

MAX_SEARCH_RESULTS = 20
DEFAULT_SEARCH_LIMIT = 5
BROWSE_MAX_RESULTS = 100

DetailLevel = Literal["brief", "detailed", "full"]


class SearchToolsInput(BaseModel):
    query: str = Field(
        default="",
        description="What you want to do, or part of a tool name. Leave empty to browse the catalog.",
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        ge=1,
        description=f"Maximum results. Values above {MAX_SEARCH_RESULTS} are reduced to {MAX_SEARCH_RESULTS}.",
    )
    detail: DetailLevel = Field(
        default="detailed",
        description="'brief' = name and one line, 'detailed' = parameters, 'full' = complete schema.",
    )


class CallToolInput(BaseModel):
    name: str = Field(min_length=1, description="Exact name of the tool to run, as returned by search_tools.")
    arguments: Dict[str, Any] = Field(
        default_factory=dict, description="Arguments for that tool, matching its input schema."
    )


@dataclass
class RankOutcome:
    """Result of ranking: (tool, score) pairs, best first, plus an optional explanation."""

    ranked: List[Tuple[Any, float]] = field(default_factory=list)
    note: Optional[str] = None