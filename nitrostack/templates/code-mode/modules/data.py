"""One numerical micro-tool for composing a multi-tool script."""
from nitrostack import ExecutionContext, injectable, tool
from pydantic import BaseModel, Field, FiniteFloat
from modules.models import READ_ONLY


class SumInput(BaseModel):
    values: list[FiniteFloat] = Field(description="Finite numbers to sum", max_length=1000)


@injectable()
class DataTools:
    @tool(name="data_aggregate_sum", description="Sum a list of numeric values from earlier tool results", input_schema=SumInput, annotations=READ_ONLY)
    async def aggregate_sum(self, input: SumInput, context: ExecutionContext) -> dict:
        return {"total": sum(input.values)}
