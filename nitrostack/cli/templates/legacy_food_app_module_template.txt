from nitrostack import module
from modules.food_delivery.food_delivery_module import FoodDeliveryModule

@module(
    name="app",
    imports=[FoodDeliveryModule],
    controllers=[],
    providers=[],
    exports=[]
)
class AppModule:
    pass
