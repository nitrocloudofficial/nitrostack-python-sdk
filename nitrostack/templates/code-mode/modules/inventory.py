"""Deterministic sample inventory, shared by discovery and orchestration examples."""
from nitrostack import ExecutionContext, injectable, tool
from pydantic import BaseModel, Field
from modules.models import EmptyInput, READ_ONLY

STOCK = {"keyboard": 12, "monitor": 6}


class StockInput(BaseModel):
    sku: str = Field(description="Product SKU: keyboard or monitor")


@injectable()
class InventoryTools:
    @tool(name="inventory_check_stock", description="Look up available stock quantity for a product SKU", input_schema=StockInput, annotations=READ_ONLY)
    async def check_stock(self, input: StockInput, context: ExecutionContext) -> dict:
        if input.sku not in STOCK:
            raise ValueError("Unknown demonstration SKU; use keyboard or monitor")
        return {"sku": input.sku, "quantity": STOCK[input.sku]}

    @tool(name="inventory_report", description="Report all demonstration warehouse inventory quantities", input_schema=EmptyInput, annotations=READ_ONLY)
    async def report(self, input: EmptyInput, context: ExecutionContext) -> dict:
        return {"stock": dict(STOCK)}
