"""Local sample support tickets; no authentication or ticketing service."""
from nitrostack import ExecutionContext, injectable, tool
from pydantic import BaseModel, Field
from modules.models import EmptyInput, READ_ONLY

TICKETS = (
    {"id": "T-001", "title": "Keyboard delivery delayed", "status": "open"},
    {"id": "T-002", "title": "Monitor setup question", "status": "resolved"},
)


class TicketInput(BaseModel):
    query: str = Field(default="", description="Words to find in a support ticket title")


@injectable()
class SupportTools:
    @tool(name="support_get_system_status", description="Check demonstration customer support system health", input_schema=EmptyInput, annotations=READ_ONLY)
    async def status(self, input: EmptyInput, context: ExecutionContext) -> dict:
        return {"status": "ok", "demo": True}

    @tool(name="support_search_tickets", description="Search customer support tickets about deliveries and product setup", input_schema=TicketInput, annotations=READ_ONLY)
    async def tickets(self, input: TicketInput, context: ExecutionContext) -> dict:
        return {"tickets": [dict(row) for row in TICKETS if input.query.casefold() in row["title"].casefold()]}
