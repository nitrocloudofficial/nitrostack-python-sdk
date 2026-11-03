"""Fixed demonstration invoices; no banking or payment integration."""
from nitrostack import ExecutionContext, injectable, tool
from pydantic import BaseModel, Field
from modules.models import EmptyInput, READ_ONLY

INVOICES = (
    {"id": "INV-001", "customer": "Acme", "amount": 1200, "overdue": True},
    {"id": "INV-002", "customer": "Example Co", "amount": 800, "overdue": False},
)


class InvoiceInput(BaseModel):
    overdue_only: bool = Field(default=False, description="Return only overdue unpaid invoices")


@injectable()
class FinanceTools:
    @tool(name="finance_get_financial_summary", description="Summarize demonstration revenue and invoice totals", input_schema=EmptyInput, annotations=READ_ONLY)
    async def summary(self, input: EmptyInput, context: ExecutionContext) -> dict:
        return {"invoice_count": len(INVOICES), "total_amount": sum(row["amount"] for row in INVOICES)}

    @tool(name="finance_list_invoices", description="Find unpaid invoices, including overdue customer invoices", input_schema=InvoiceInput, annotations=READ_ONLY)
    async def invoices(self, input: InvoiceInput, context: ExecutionContext) -> dict:
        return {"invoices": [dict(row) for row in INVOICES if not input.overdue_only or row["overdue"]]}
