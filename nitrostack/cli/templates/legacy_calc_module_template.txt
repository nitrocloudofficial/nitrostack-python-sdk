from nitrostack import module
from modules.calculator.calculator_tools import CalculatorController
from modules.calculator.calculator_service import CalculatorService

@module(
    name="calculator",
    imports=[],
    controllers=[CalculatorController],
    providers=[CalculatorService],
    exports=[CalculatorService]
)
class CalculatorModule:
    pass
