"""BM25 progressive-discovery server with local sample tools."""
import asyncio
from nitrostack import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.transforms.search import Bm25SearchTransform
from app_module import AppModule


@mcp_app(
    module=AppModule,
    server=ServerConfig(name="enterprise-search"),
    transforms=[Bm25SearchTransform(always_visible=[
        "finance_get_financial_summary", "inventory_report", "support_get_system_status",
    ])],
)
class Application:
    pass


async def main():
    app = await McpApplicationFactory.create(Application)
    await app.start()


if __name__ == "__main__":
    asyncio.run(main())
