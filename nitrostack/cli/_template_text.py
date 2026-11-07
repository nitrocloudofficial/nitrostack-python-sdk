"""Compatibility template strings, stored as package text assets."""

from importlib.resources import files


def _read(name: str) -> str:
    return files("nitrostack.cli.templates").joinpath(name).read_text(encoding="utf-8")


MAIN_TEMPLATE = _read("legacy_main_template.txt")
APP_MODULE_TEMPLATE = _read("legacy_app_module_template.txt")
CALC_MODULE_TEMPLATE = _read("legacy_calc_module_template.txt")
CALC_SERVICE_TEMPLATE = _read("legacy_calc_service_template.txt")
CALC_TOOLS_TEMPLATE = _read("legacy_calc_tools_template.txt")
FOOD_APP_MODULE_TEMPLATE = _read("legacy_food_app_module_template.txt")
FOOD_DELIVERY_MODULE_TEMPLATE = _read("legacy_food_delivery_module_template.txt")
FOOD_DELIVERY_SERVICE_TEMPLATE = _read("legacy_food_delivery_service_template.txt")
FOOD_DELIVERY_TOOLS_TEMPLATE = _read("legacy_food_delivery_tools_template.txt")
FLIGHT_APP_MODULE_TEMPLATE = _read("legacy_flight_app_module_template.txt")
FLIGHT_OAUTH_GUARD_TEMPLATE = _read("legacy_flight_oauth_guard_template.txt")
FLIGHT_SYSTEM_HEALTH_TEMPLATE = _read("legacy_flight_system_health_template.txt")
FLIGHT_DUFFEL_SERVICE_TEMPLATE = _read("legacy_flight_duffel_service_template.txt")
FLIGHT_MODULE_TEMPLATE = _read("legacy_flight_module_template.txt")
FLIGHT_TOOLS_TEMPLATE = _read("legacy_flight_tools_template.txt")
FLIGHT_BOOKING_TOOLS_TEMPLATE = _read("legacy_flight_booking_tools_template.txt")
FLIGHT_PROMPTS_TEMPLATE = _read("legacy_flight_prompts_template.txt")
FLIGHT_RESOURCES_TEMPLATE = _read("legacy_flight_resources_template.txt")
OAUTH_SETUP_TEMPLATE = _read("legacy_oauth_setup_template.txt")
ENV_TEMPLATE = _read("legacy_env_template.txt")
REQUIREMENTS_TEMPLATE = _read("legacy_requirements_template.txt")
TOOL_TEMPLATE = _read("legacy_tool_template.txt")
WIDGET_PREVIEW_HTML = _read("legacy_widget_preview_html.txt")
SAMPLE_PIZZA_PREVIEW_JSON = _read("legacy_sample_pizza_preview_json.txt")
MODULE_TEMPLATE = _read("legacy_module_template.txt")
