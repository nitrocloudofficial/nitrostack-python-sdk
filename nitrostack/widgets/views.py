"""Python-rendered widget bodies from tool ``structuredContent``.

Hosts that inject data later still use the HTML document's small host-bridge
script. First paint (Inspector ``tools/call`` HTML, live preview) uses these
builders so the iframe matches the actual tool output — not sample JSON.

This module is the route -> body dispatcher. The bodies live in views_common,
views_pizza and views_flight and are re-exported here, so existing imports keep working.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

# Re-exported only so existing ``from nitrostack.widgets.views import ...`` lines keep working.
from nitrostack.widgets.html_util import (  # noqa: F401
    as_dict,
    as_list,
    esc,
    format_duration,
    mapbox_static_url,
    money,
    pick,
    place_code,
    place_name,
)
from nitrostack.widgets.ui import action_row, call_attrs, maps_url  # noqa: F401
from nitrostack.widgets.views_common import (  # noqa: F401
    _safe_int,
    _safe_float,
    calculator_result_body,
    card_body,
    table_body,
    chart_body,
    generic_body,
    missing_body,
)
from nitrostack.widgets.views_pizza import (  # noqa: F401
    pizza_list_body,
    _pizza_map_card,
    pizza_map_body,
    pizza_shop_body,
)
from nitrostack.widgets.views_flight import (  # noqa: F401
    _offer_slice_html,
    _offer_itinerary_html,
    flight_search_results_body,
    flight_details_body,
    airport_search_body,
    order_summary_body,
    _seat_cells,
    seat_selection_body,
    order_cancellation_body,
)


BODY_BUILDERS: Dict[str, Callable[[Any | None], str]] = {
    "pizza-list": pizza_list_body,
    "pizza-map": pizza_map_body,
    "pizza-shop": pizza_shop_body,
    "calculator-result": calculator_result_body,
    "card": card_body,
    "table": table_body,
    "chart": chart_body,
    "flight-search-results": flight_search_results_body,
    "flight-details": flight_details_body,
    "airport-search": airport_search_body,
    "order-summary": order_summary_body,
    "seat-selection": seat_selection_body,
    "order-cancellation": order_cancellation_body,
}


def render_body(route: str, data: Any | None = None) -> str:
    builder = BODY_BUILDERS.get(route)
    if builder is None:
        return generic_body(route, data)
    return builder(data)
