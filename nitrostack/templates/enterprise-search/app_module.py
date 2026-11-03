from nitrostack import ConfigModule, module
from modules.finance import FinanceTools
from modules.inventory import InventoryTools
from modules.support import SupportTools


@module(
    name="app",
    imports=[ConfigModule.for_root(env_file_path=".env", defaults={"PORT": "3000"})],
    controllers=[FinanceTools, InventoryTools, SupportTools],
)
class AppModule:
    pass
