"""Shared schemas and read-only annotations for the demonstration tools."""
from nitrostack import ToolAnnotations
from pydantic import BaseModel

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


class EmptyInput(BaseModel):
    pass
