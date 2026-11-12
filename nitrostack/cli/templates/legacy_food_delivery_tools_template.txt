from nitrostack import injectable, tool, ExecutionContext
from modules.food_delivery.food_delivery_service import FoodDeliveryService
from pydantic import BaseModel, Field

class ViewMenuInput(BaseModel):
    pass

class PlaceOrderInput(BaseModel):
    item: str = Field(description="The food item to order (e.g., pizza, burger, salad, sushi)")
    quantity: int = Field(default=1, description="Number of items to order")

class OrderStatusInput(BaseModel):
    order_id: str = Field(description="The ID of the order to track (e.g., ORDER-1001)")

@injectable(deps=[FoodDeliveryService])
class FoodDeliveryController:
    def __init__(self, service: FoodDeliveryService):
        self.service = service

    @tool(
        name="view_menu",
        description="View the menu and list available food items and prices",
        input_schema=ViewMenuInput
    )
    async def view_menu(self, input: ViewMenuInput, context: ExecutionContext) -> dict:
        context.logger.info("Fetching food delivery menu...")
        return {"menu": self.service.get_menu()}

    @tool(
        name="place_order",
        description="Place a food delivery order for a menu item",
        input_schema=PlaceOrderInput
    )
    async def place_order(self, input: PlaceOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Placing order for {input.quantity}x {input.item}")
        return self.service.place_order(input.item, input.quantity)

    @tool(
        name="track_order",
        description="Track the status of an existing food delivery order",
        input_schema=OrderStatusInput
    )
    async def track_order(self, input: OrderStatusInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Tracking order status for {input.order_id}")
        return self.service.get_order_status(input.order_id)
