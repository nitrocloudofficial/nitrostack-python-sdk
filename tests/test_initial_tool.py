import asyncio
import os
import sys
from pydantic import BaseModel

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import module, injectable, tool, initial_tool, ExecutionContext, NitroTestingModule
import mcp.types as types

class ExecutionState:
    called = False

class EmptyInput(BaseModel):
    pass

@injectable(deps=[])
class InitialToolController:
    @initial_tool
    @tool(
        name="on_init_tool",
        description="Fires on connect",
        input_schema=EmptyInput
    )
    async def on_init(self, input: EmptyInput, context: ExecutionContext) -> str:
        ExecutionState.called = True
        return "Initial tool called!"

@module(
    name="test_initial",
    controllers=[InitialToolController],
    providers=[],
    imports=[],
    exports=[]
)
class TestInitialModule:
    pass

async def _test_initial_tool_hook():
    print("Testing @initial_tool auto-call hook...")
    
    # Create testing harness
    harness = await NitroTestingModule.create(TestInitialModule)
    
    # Check that called is False initially
    assert ExecutionState.called is False
    
    # Retrieve the owned low-level Server directly.
    server = harness.app.mcp_server
    
    # Get the InitializedNotification handler
    handler = server.notification_handlers[types.InitializedNotification]
    
    # Simulate InitializedNotification from client
    notification = types.InitializedNotification(
        method="notifications/initialized",
        params=None
    )
    
    # Await the initialized notification handler
    await handler(notification)
    
    # Assert that ExecutionState.called is now True!
    assert ExecutionState.called is True
    print("Test passed! @initial_tool was automatically called when InitializedNotification arrived.")

def test_initial_tool_hook():
    asyncio.run(_test_initial_tool_hook())

if __name__ == "__main__":
    test_initial_tool_hook()
