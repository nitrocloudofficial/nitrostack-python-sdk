from nitrostack import injectable, tool, ExecutionContext
from modules.calculator.calculator_service import CalculatorService
from pydantic import BaseModel

class AddInput(BaseModel):
    a: float
    b: float

@injectable(deps=[CalculatorService])
class CalculatorController:
    def __init__(self, service: CalculatorService):
        self.service = service

    @tool(
        name="add",
        description="Add two numbers together",
        input_schema=AddInput
    )
    async def add(self, input: AddInput, context: ExecutionContext) -> float:
        context.logger.info(f"Adding {input.a} and {input.b}")
        return self.service.add(input.a, input.b)
