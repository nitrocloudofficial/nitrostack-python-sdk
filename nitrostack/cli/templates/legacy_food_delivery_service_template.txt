from nitrostack import injectable

@injectable(deps=[])
class FoodDeliveryService:
    def __init__(self):
        # In-memory database of items and orders
        self.menu = {
            "pizza": {"price": 12.99, "prep_time": 15},
            "burger": {"price": 8.99, "prep_time": 10},
            "salad": {"price": 7.49, "prep_time": 5},
            "sushi": {"price": 15.99, "prep_time": 20}
        }
        self.orders = {}
        self.order_counter = 1000

    def get_menu(self):
        return self.menu

    def place_order(self, item: str, quantity: int) -> dict:
        item_lower = item.lower()
        if item_lower not in self.menu:
            return {"status": "error", "message": f"Item '{item}' not found in the menu."}
        
        self.order_counter += 1
        order_id = f"ORDER-{self.order_counter}"
        
        price = self.menu[item_lower]["price"] * quantity
        prep_time = self.menu[item_lower]["prep_time"]
        
        self.orders[order_id] = {
            "order_id": order_id,
            "item": item_lower,
            "quantity": quantity,
            "total_price": round(price, 2),
            "status": "Preparing",
            "time_remaining": prep_time
        }
        return self.orders[order_id]

    def get_order_status(self, order_id: str) -> dict:
        return self.orders.get(order_id, {"status": "error", "message": f"Order {order_id} not found."})
