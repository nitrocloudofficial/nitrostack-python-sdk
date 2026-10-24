"""Catalog used by both the baseline capture script and the parity test."""
from pydantic import BaseModel

from nitrostack import ExecutionContext, injectable, module, tool

ERAS = ("legacy", "auto", "modern")


class In(BaseModel):
    q: str = ""


@injectable()
class C:
    @tool(name="alpha", description="first", input_schema=In)
    async def alpha(self, input: In, context: ExecutionContext) -> str:
        return "a"

    @tool(name="beta", description="second", input_schema=In)
    async def beta(self, input: In, context: ExecutionContext) -> str:
        return "b"


@module(name="m", controllers=[C])
class M:
    pass