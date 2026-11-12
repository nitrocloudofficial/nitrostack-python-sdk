from nitrostack import module
from modules.calculator.calculator_module import CalculatorModule

@module(
    name="app",
    imports=[CalculatorModule],
    controllers=[],
    providers=[],
    exports=[]
)
class AppModule:
    pass
