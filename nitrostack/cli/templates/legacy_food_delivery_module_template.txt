from nitrostack import module
from modules.food_delivery.food_delivery_tools import FoodDeliveryController
from modules.food_delivery.food_delivery_service import FoodDeliveryService

@module(
    name="food_delivery",
    imports=[],
    controllers=[FoodDeliveryController],
    providers=[FoodDeliveryService],
    exports=[FoodDeliveryService]
)
class FoodDeliveryModule:
    pass
