from nitrostack import injectable

@injectable(deps=[])
class CalculatorService:
    def add(self, a: float, b: float) -> float:
        return a + b
