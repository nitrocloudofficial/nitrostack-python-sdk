"""MCP Inspector form rendering: tool inputSchema must be top-level fields.

Inspector renders `inputSchema.properties` as form widgets. Nesting the model
under `properties.input.$ref` only showed labels. Pydantic Optional fields
(`anyOf: [T, null]`) also skip Inspector widgets.
"""
import asyncio
import os
import sys
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mcp.types as types
from nitrostack import injectable, module, tool, ExecutionContext, NitroTestingModule
from nitrostack.core.app import inspector_friendly_schema, parse_tool_input


class ShowListInput(BaseModel):
    openNow: Optional[bool] = Field(default=None, description="Show only shops that are currently open")
    minRating: Optional[float] = Field(default=None, description="Minimum rating (1-5)")
    maxPrice: Optional[float] = Field(default=None, description="Maximum price level (1-3)")


class ShowMapInput(BaseModel):
    filter: Literal["open_now", "top_rated", "all"] = Field(default="all", description="Filter to apply")


class ShowShopInput(BaseModel):
    shopId: str = Field(description="ID of the pizza shop to display")


class CalculateInput(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"] = Field(description="The operation to perform")
    a: float = Field(description="First number")
    b: float = Field(description="Second number")


class SearchFlightsInput(BaseModel):
    origin: str = Field(description="Origin airport IATA code")
    destination: str = Field(description="Destination airport IATA code")
    departureDate: str = Field(description="Departure date in YYYY-MM-DD format")
    returnDate: Optional[str] = Field(default=None, description="Return date")
    adults: int = Field(default=1, description="Number of adult passengers")
    cabinClass: str = Field(default="economy", description="Preferred cabin class")


class OrderItem(BaseModel):
    item_name: str = Field(description="Name of the food item")
    quantity: int = Field(default=1, description="Quantity")
    notes: Optional[str] = Field(default=None, description="Special instructions")


class PlaceOrderInput(BaseModel):
    items: List[OrderItem] = Field(description="List of food items")
    address: str = Field(description="Delivery address")
    payment_method: Literal["cash", "card", "paypal"] = Field(description="Payment method")


class EmptyInput(BaseModel):
    pass


@injectable()
class SchemaTools:
    @tool(name="show_pizza_list", description="list", input_schema=ShowListInput)
    async def show_pizza_list(self, input: ShowListInput, context: ExecutionContext) -> dict:
        return {"openNow": input.openNow, "minRating": input.minRating, "maxPrice": input.maxPrice}

    @tool(name="show_pizza_map", description="map", input_schema=ShowMapInput)
    async def show_pizza_map(self, input: ShowMapInput, context: ExecutionContext) -> dict:
        return {"filter": input.filter}

    @tool(name="show_pizza_shop", description="shop", input_schema=ShowShopInput)
    async def show_pizza_shop(self, input: ShowShopInput, context: ExecutionContext) -> dict:
        return {"shopId": input.shopId}

    @tool(name="calculate", description="calc", input_schema=CalculateInput)
    async def calculate(self, input: CalculateInput, context: ExecutionContext) -> dict:
        return {"a": input.a, "b": input.b, "operation": input.operation}

    @tool(name="search_flights", description="flights", input_schema=SearchFlightsInput)
    async def search_flights(self, input: SearchFlightsInput, context: ExecutionContext) -> dict:
        return {"origin": input.origin, "destination": input.destination}

    @tool(name="place_order", description="order", input_schema=PlaceOrderInput)
    async def place_order(self, input: PlaceOrderInput, context: ExecutionContext) -> dict:
        return {"address": input.address, "count": len(input.items)}

    @tool(name="get_airlines", description="airlines", input_schema=EmptyInput)
    async def get_airlines(self, input: EmptyInput, context: ExecutionContext) -> dict:
        return {"airlines": []}


@module(name="schema_tools", controllers=[SchemaTools])
class SchemaToolsModule:
    pass


def _assert_no_nullable_union(node: Any, path: str = "$") -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _assert_no_nullable_union(item, f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return
    for key in ("anyOf", "oneOf"):
        variants = node.get(key)
        if isinstance(variants, list) and any(
            isinstance(item, dict) and item.get("type") == "null" for item in variants
        ):
            raise AssertionError(f"{path} still has nullable {key}: {variants}")
    types = node.get("type")
    if isinstance(types, list) and "null" in types:
        raise AssertionError(f"{path} still has nullable type list: {types}")
    for key, value in node.items():
        _assert_no_nullable_union(value, f"{path}.{key}")


def _assert_inspector_schema(schema: Dict[str, Any], expected_fields: List[str]) -> None:
    assert schema.get("type") == "object", schema
    properties = schema.get("properties") or {}
    assert list(properties.keys()) != ["input"] or not isinstance(properties.get("input"), dict) or not properties["input"].get("$ref"), (
        "inputSchema is still wrapped under properties.input.$ref; Inspector cannot render form fields"
    )
    for name in expected_fields:
        assert name in properties, f"missing field {name} in {list(properties)}"
        field = properties[name]
        assert "anyOf" not in field, f"{name} still uses anyOf: {field}"
        assert "oneOf" not in field, f"{name} still uses oneOf: {field}"
        assert (
            field.get("type")
            or field.get("enum")
            or field.get("$ref")
            or field.get("items")
            or field.get("properties")
        ), f"{name} is not a renderable JSON Schema: {field}"
    _assert_no_nullable_union(schema)


def test_optional_fields_flatten_to_concrete_types():
    raw = ShowListInput.model_json_schema()
    assert "anyOf" in raw["properties"]["openNow"]
    schema = inspector_friendly_schema(raw)
    _assert_inspector_schema(schema, ["openNow", "minRating", "maxPrice"])
    assert schema["properties"]["openNow"]["type"] == "boolean"
    assert schema["properties"]["minRating"]["type"] == "number"
    assert schema["properties"]["maxPrice"]["type"] == "number"
    assert "openNow" not in (schema.get("required") or [])


def test_literal_enum_and_nested_models_stay_renderable():
    map_schema = inspector_friendly_schema(ShowMapInput.model_json_schema())
    _assert_inspector_schema(map_schema, ["filter"])
    assert map_schema["properties"]["filter"]["enum"] == ["open_now", "top_rated", "all"]

    calc_schema = inspector_friendly_schema(CalculateInput.model_json_schema())
    _assert_inspector_schema(calc_schema, ["operation", "a", "b"])
    assert calc_schema["properties"]["operation"]["enum"] == ["add", "subtract", "multiply", "divide"]

    flight_schema = inspector_friendly_schema(SearchFlightsInput.model_json_schema())
    _assert_inspector_schema(
        flight_schema,
        ["origin", "destination", "departureDate", "returnDate", "adults", "cabinClass"],
    )
    assert flight_schema["properties"]["returnDate"]["type"] == "string"

    order_schema = inspector_friendly_schema(PlaceOrderInput.model_json_schema())
    _assert_inspector_schema(order_schema, ["items", "address", "payment_method"])
    assert order_schema["properties"]["items"]["type"] == "array"


def test_parse_tool_input_accepts_inspector_and_legacy_wrap():
    inspector_args = parse_tool_input(ShowListInput, {"openNow": True, "minRating": 4.0})
    assert inspector_args.openNow is True
    assert inspector_args.minRating == 4.0

    legacy_args = parse_tool_input(ShowListInput, {"input": {"openNow": False, "maxPrice": 2}})
    assert legacy_args.openNow is False
    assert legacy_args.maxPrice == 2

    empty_args = parse_tool_input(ShowListInput, {})
    assert empty_args.openNow is None

    shop = parse_tool_input(ShowShopInput, {"shopId": "tonys-pizza"})
    assert shop.shopId == "tonys-pizza"
    shop_wrapped = parse_tool_input(ShowShopInput, {"input": {"shopId": "bella-napoli"}})
    assert shop_wrapped.shopId == "bella-napoli"

    # Inspector leaves unused enum/optional fields as "".
    blank_map = parse_tool_input(ShowMapInput, {"filter": ""})
    assert blank_map.filter == "all"
    whitespace_map = parse_tool_input(ShowMapInput, {"filter": "   "})
    assert whitespace_map.filter == "all"
    missing_map = parse_tool_input(ShowMapInput, {})
    assert missing_map.filter == "all"
    wrapped_blank = parse_tool_input(ShowMapInput, {"input": {"filter": ""}})
    assert wrapped_blank.filter == "all"
    explicit_all = parse_tool_input(ShowMapInput, {"filter": "all"})
    assert explicit_all.filter == "all"
    open_now = parse_tool_input(ShowMapInput, {"filter": "open_now"})
    assert open_now.filter == "open_now"

    blank_list = parse_tool_input(ShowListInput, {"openNow": "", "minRating": "", "maxPrice": ""})
    assert blank_list.openNow is None
    assert blank_list.minRating is None
    assert blank_list.maxPrice is None

    try:
        parse_tool_input(ShowShopInput, {"shopId": ""})
        raise AssertionError("required shopId must still reject an empty string")
    except Exception:
        pass


async def _listed_tools() -> Dict[str, types.Tool]:
    harness = await NitroTestingModule.create(SchemaToolsModule)
    handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
    listed = await handler(None)
    return {item.name: item for item in listed.root.tools}


def test_listed_tools_expose_top_level_fields_not_input_wrap():
    tools = asyncio.run(_listed_tools())

    expected = {
        "show_pizza_list": ["openNow", "minRating", "maxPrice"],
        "show_pizza_map": ["filter"],
        "show_pizza_shop": ["shopId"],
        "calculate": ["operation", "a", "b"],
        "search_flights": ["origin", "destination", "departureDate", "returnDate", "adults", "cabinClass"],
        "place_order": ["items", "address", "payment_method"],
        "get_airlines": [],
    }
    assert set(tools) >= set(expected)
    for name, fields in expected.items():
        schema = tools[name].input_schema
        _assert_inspector_schema(schema, fields)
        properties = schema.get("properties") or {}
        if fields:
            assert "input" not in properties, f"{name} still nests fields under 'input'"


def test_call_tool_accepts_top_level_and_wrapped_arguments():
    async def _run():
        harness = await NitroTestingModule.create(SchemaToolsModule)
        listed = await harness.call_tool("show_pizza_list", {"openNow": True, "minRating": 4.5})
        assert listed["openNow"] is True
        assert listed["minRating"] == 4.5

        wrapped = await harness.call_tool("show_pizza_list", {"input": {"openNow": False, "maxPrice": 1}})
        assert wrapped["openNow"] is False
        assert wrapped["maxPrice"] == 1

        shop = await harness.call_tool("show_pizza_shop", {"shopId": "tonys-pizza"})
        assert shop["shopId"] == "tonys-pizza"

        calc = await harness.call_tool("calculate", {"operation": "add", "a": 2, "b": 3})
        assert calc["operation"] == "add"

        calc_wrapped = await harness.call_tool(
            "calculate", {"input": {"operation": "multiply", "a": 4, "b": 5}}
        )
        assert calc_wrapped["operation"] == "multiply"

        # Inspector pizza-map form sends filter="" when left empty / "all".
        mapped = await harness.call_tool("show_pizza_map", {"filter": ""})
        assert mapped["filter"] == "all"
        mapped_all = await harness.call_tool("show_pizza_map", {"filter": "all"})
        assert mapped_all["filter"] == "all"
        mapped_open = await harness.call_tool("show_pizza_map", {"filter": "open_now"})
        assert mapped_open["filter"] == "open_now"

    asyncio.run(_run())


def _load_py(module_name: str, path: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_stub(fullname: str, **attrs):
    import types as pytypes

    parts = fullname.split(".")
    for index in range(1, len(parts) + 1):
        name = ".".join(parts[:index])
        if name not in sys.modules:
            sys.modules[name] = pytypes.ModuleType(name)
            parent = ".".join(parts[: index - 1])
            if parent:
                setattr(sys.modules[parent], parts[index - 1], sys.modules[name])
    for key, value in attrs.items():
        setattr(sys.modules[fullname], key, value)
    return sys.modules[fullname]


def test_example_and_template_input_models():
    from examples.calculator_server import CalculateInput as ExCalc, ConvertTempInput, LongRunningInput
    from examples.main import GreetInput
    from examples.food_order_server import PlaceOrderInput as ExOrder, CancelOrderInput as ExCancel
    from examples.flight_booking_server import (
        SearchFlightsInput as ExFlights,
        FlightDetailsInput,
        AirportSearchInput,
        CreateOrderInput,
        OrderDetailsInput,
        SeatMapInput,
        CancelOrderInput as ExCancelFlight,
        GetAirlinesInput,
    )

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    starter = _load_py(
        "starter_calculator_tools_under_test",
        os.path.join(root, "nitrostack", "templates", "starter", "modules", "calculator", "calculator_tools.py"),
    )

    cases = [
        (ExCalc, ["operation", "a", "b"]),
        (ConvertTempInput, ["value", "from_unit", "to_unit"]),
        (LongRunningInput, ["steps", "delay_seconds"]),
        (GreetInput, ["name"]),
        (ExOrder, ["items", "address", "payment_method"]),
        (ExCancel, ["order_id", "reason"]),
        (ExFlights, ["origin", "destination", "departureDate", "returnDate", "adults", "cabinClass"]),
        (FlightDetailsInput, ["offerId"]),
        (AirportSearchInput, ["query"]),
        (CreateOrderInput, ["offerId", "passengers"]),
        (OrderDetailsInput, ["orderId"]),
        (SeatMapInput, ["offerId"]),
        (ExCancelFlight, ["orderId"]),
        (GetAirlinesInput, []),
        (starter.CalculateInput, ["operation", "a", "b"]),
        (starter.ConvertTemperatureInput, ["value", "from_unit", "to_unit"]),
    ]
    for model, fields in cases:
        schema = inspector_friendly_schema(model.model_json_schema())
        _assert_inspector_schema(schema, fields)


def test_pizzaz_and_oauth_template_input_models():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    class _DummyService:
        pass

    _ensure_stub("modules.pizzaz.pizzaz_service", PizzazService=_DummyService)
    pizzaz = _load_py(
        "pizzaz_tools_under_test",
        os.path.join(root, "nitrostack", "templates", "pizzaz", "modules", "pizzaz", "pizzaz_tools.py"),
    )

    _ensure_stub("services.duffel_service", DuffelService=_DummyService)
    _ensure_stub("guards.oauth_guard", create_scope_guard=lambda scopes: (lambda: None))
    flights = _load_py(
        "flights_tools_under_test",
        os.path.join(root, "nitrostack", "templates", "flight-booking", "modules", "flights", "flights_tools.py"),
    )
    booking = _load_py(
        "booking_tools_under_test",
        os.path.join(root, "nitrostack", "templates", "flight-booking", "modules", "flights", "booking_tools.py"),
    )

    cases = [
        (pizzaz.ShowListInput, ["openNow", "minRating", "maxPrice"]),
        (pizzaz.ShowMapInput, ["filter"]),
        (pizzaz.ShowShopInput, ["shopId"]),
        (flights.SearchFlightsInput, ["origin", "destination", "departureDate", "returnDate", "adults", "cabinClass"]),
        (flights.FlightDetailsInput, ["offerId"]),
        (flights.AirportSearchInput, ["query"]),
        (flights.GetAirlinesInput, []),
        (booking.CreateOrderInput, ["offerId", "passengers"]),
        (booking.OrderDetailsInput, ["orderId"]),
        (booking.SeatMapInput, ["offerId"]),
        (booking.CancelOrderInput, ["orderId"]),
    ]
    for model, fields in cases:
        schema = inspector_friendly_schema(model.model_json_schema())
        _assert_inspector_schema(schema, fields)


if __name__ == "__main__":
    test_optional_fields_flatten_to_concrete_types()
    test_literal_enum_and_nested_models_stay_renderable()
    test_parse_tool_input_accepts_inspector_and_legacy_wrap()
    test_listed_tools_expose_top_level_fields_not_input_wrap()
    test_call_tool_accepts_top_level_and_wrapped_arguments()
    test_example_and_template_input_models()
    test_pizzaz_and_oauth_template_input_models()
    print("Success! Tool input schemas are Inspector-renderable.")
