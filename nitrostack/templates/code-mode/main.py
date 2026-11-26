"""Code-mode server; importing this module never starts workers or transports."""
import asyncio
from nitrostack import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.transforms.code_mode import CodeModeTransform
from app_module import AppModule

CODE_MODE = CodeModeTransform(
    timeout_ms=15000,
    memory_limit_mb=100,
    max_tool_calls=20,
    worker_pool_size=1,
    allow_destructive=False,
)


@mcp_app(module=AppModule, server=ServerConfig(name="code-mode"), transforms=[CODE_MODE])
class Application:
    pass


async def main():
    try:
        app = await McpApplicationFactory.create(Application)
        await app.start()
    finally:
        await CODE_MODE.dispose()


if __name__ == "__main__":
    asyncio.run(main())
