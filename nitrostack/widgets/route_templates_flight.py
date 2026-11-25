"""Flight and order widget templates (CSS and render JS)."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.route_templates_generic import _page
from nitrostack.widgets.views import render_body


_FLIGHT_CSS = """
    :root { --ns-accent: #0369a1; --ns-accent-soft: #e0f2fe; }
    .offer, .card, #list .row { background: var(--ns-surface); border: 1px solid var(--ns-border); border-radius: var(--ns-radius); padding: 12px; margin-bottom: 10px; }
    .row { display: flex; gap: 12px; align-items: center; }
    .id { font-size: 0.8rem; color: var(--ns-muted); }
    .code { font-size: 1.1rem; font-weight: 800; min-width: 3rem; }
    .slice { margin-top: 8px; }
    .seat-row { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
    .rn { width: 2rem; font-size: 0.8rem; color: var(--ns-muted); }
    .seat { display: inline-block; min-width: 2.4rem; text-align: center; padding: 4px; border-radius: 6px; font-size: 0.75rem; }
"""


_FLIGHT_SEARCH_JS = """
window.__nitroWidgetRender = function(data) {
  const list = document.getElementById("list");
  const meta = document.getElementById("meta");
  if (!list) return;
  const offers = (data && (data.offers || data.results)) || [];
  const params = (data && data.searchParams) || {};
  const total = (data && data.totalOffers != null) ? data.totalOffers : offers.length;
  if (meta) {
    meta.textContent = (params.origin && params.destination)
      ? (params.origin + " → " + params.destination + " · " + total + " offer" + (total === 1 ? "" : "s"))
      : (total + " offer" + (total === 1 ? "" : "s"));
  }
  list.innerHTML = "";
  if (!offers.length) {
    list.innerHTML = '<div class="empty">No flight offers.</div>';
    return;
  }
  function code(p) {
    if (!p) return "";
    if (typeof p === "string") return p;
    return p.iata_code || p.iataCode || p.code || "";
  }
  function legs(offer) {
    if (offer.slices && offer.slices.length) return offer.slices;
    const out = [];
    if (offer.outbound) out.push(offer.outbound);
    if (offer.return) out.push(offer.return);
    return out;
  }
  offers.forEach(function(offer) {
    const el = document.createElement("article");
    el.className = "offer ns-card";
    el.setAttribute("data-call-tool", "get_flight_details");
    el.setAttribute("data-args", JSON.stringify({ offerId: offer.id || "" }));
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    const amount = offer.total_amount || offer.totalAmount || "";
    const currency = offer.total_currency || offer.totalCurrency || "";
    el.innerHTML = '<div class="row"><span class="price"></span><span class="id"></span></div>';
    el.querySelector(".price").textContent = amount + (currency ? " " + currency : "");
    el.querySelector(".id").textContent = offer.id || "";
    legs(offer).forEach(function(sl) {
      const s = document.createElement("div");
      s.className = "slice";
      s.textContent = code(sl.origin) + " → " + code(sl.destination) + " · " + (sl.duration || "");
      el.appendChild(s);
    });
    list.appendChild(el);
  });
};
""".strip()


_AIRPORT_JS = """
window.__nitroWidgetRender = function(data) {
  const list = document.getElementById("list");
  const meta = document.getElementById("meta");
  if (!list) return;
  const results = (data && (data.results || data.airports || data.places)) || [];
  if (meta) meta.textContent = "Query: " + ((data && data.query) || "") + " · " + results.length + " result(s)";
  list.innerHTML = "";
  if (!results.length) {
    list.innerHTML = '<div class="empty">No airports found.</div>';
    return;
  }
  results.forEach(function(item) {
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = '<span class="code"></span><div><div class="name"></div><div class="muted"></div></div>';
    el.querySelector(".code").textContent = item.iata_code || item.iataCode || "";
    el.querySelector(".name").textContent = item.name || "";
    el.querySelector(".muted").textContent = (item.city_name || item.cityName || item.city || "") + " · " + (item.type || "airport");
    list.appendChild(el);
  });
};
""".strip()


def _place_code_js() -> str:
    return """
  function code(p) {
    if (!p) return "";
    if (typeof p === "string") return p;
    return p.iata_code || p.iataCode || p.code || "";
  }
  function money(amount, currency) {
    if (amount == null || amount === "") return "";
    return String(amount) + (currency ? " " + currency : "");
  }
  function sliceHtml(sl) {
    const origin = code(sl.origin);
    const dest = code(sl.destination);
    const wrap = document.createElement("div");
    wrap.className = "slice";
    const title = document.createElement("strong");
    title.textContent = origin + " → " + dest;
    const times = document.createElement("div");
    times.className = "muted";
    times.textContent = (sl.departing_at || sl.departingAt || sl.departureTime || "") +
      " – " + (sl.arriving_at || sl.arrivingAt || sl.arrivalTime || "") +
      " · " + (sl.duration || "");
    wrap.appendChild(title);
    wrap.appendChild(times);
    return wrap;
  }
""".strip()


_FLIGHT_DETAILS_JS = f"""
window.__nitroWidgetRender = function(data) {{
  const meta = document.getElementById("meta");
  const root = document.getElementById("root");
  if (!root) return;
  if (!data) {{
    if (meta) meta.textContent = "Waiting for get_flight_details result.";
    return;
  }}
{_place_code_js()}
  const amount = data.total_amount || data.totalAmount || "";
  const currency = data.total_currency || data.totalCurrency || "";
  if (meta) meta.textContent = "Offer " + (data.id || "") + " · " + money(amount, currency);
  root.innerHTML = "";
  (data.slices || []).forEach(function(sl) {{ root.appendChild(sliceHtml(sl)); }});
  if (data.id) {{
    const actions = document.createElement("div");
    actions.className = "ns-actions";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ns-btn ns-btn-primary";
    btn.setAttribute("data-call-tool", "get_seat_map");
    btn.setAttribute("data-args", JSON.stringify({{ offerId: data.id }}));
    btn.textContent = "Seat map";
    actions.appendChild(btn);
    root.appendChild(actions);
  }}
}};
""".strip()


_ORDER_SUMMARY_JS = f"""
window.__nitroWidgetRender = function(data) {{
  const root = document.getElementById("root");
  const card = document.querySelector(".card");
  if (!data) return;
{_place_code_js()}
  const status = data.status || "held";
  const ref = data.booking_reference || data.bookingReference || "";
  const orderId = data.id || data.orderId || "";
  const amount = data.total_amount || data.totalAmount || "";
  const currency = data.total_currency || data.totalCurrency || "";
  const host = card || root;
  if (!host) return;
  host.innerHTML = "";
  const h1 = document.createElement("h1");
  h1.textContent = "Order " + status;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = "Ref " + ref + " · " + orderId;
  const price = document.createElement("div");
  price.className = "price";
  price.textContent = money(amount, currency);
  host.appendChild(h1);
  host.appendChild(meta);
  host.appendChild(price);
  const paxTitle = document.createElement("h2");
  paxTitle.textContent = "Passengers";
  host.appendChild(paxTitle);
  const passengers = data.passengers || [];
  if (!passengers.length) {{
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "None listed";
    host.appendChild(empty);
  }}
  passengers.forEach(function(p) {{
    const line = document.createElement("div");
    line.className = "muted";
    line.textContent = p.name || [p.given_name || p.givenName, p.family_name || p.familyName].filter(Boolean).join(" ") || p.id || "";
    host.appendChild(line);
  }});
  const itin = document.createElement("h2");
  itin.textContent = "Itinerary";
  host.appendChild(itin);
  (data.slices || []).forEach(function(sl) {{ host.appendChild(sliceHtml(sl)); }});
  if (orderId && String(status).toLowerCase().indexOf("cancel") < 0) {{
    const actions = document.createElement("div");
    actions.className = "ns-actions";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ns-btn";
    btn.setAttribute("data-call-tool", "cancel_order");
    btn.setAttribute("data-args", JSON.stringify({{ orderId: orderId }}));
    btn.textContent = "Cancel order";
    actions.appendChild(btn);
    host.appendChild(actions);
  }}
}};
""".strip()


_SEAT_JS = """
window.__nitroWidgetRender = function(data) {
  const meta = document.getElementById("meta");
  const root = document.getElementById("root");
  if (!root) return;
  if (!data) {
    if (meta) meta.textContent = "Waiting for get_seat_map result.";
    return;
  }
  if (meta) meta.textContent = "Offer " + (data.offerId || data.offer_id || "");
  root.innerHTML = "";
  const cabins = data.cabins || [];
  if (!cabins.length) {
    root.innerHTML = '<div class="empty">No seat map data.</div>';
    return;
  }
  cabins.forEach(function(cabin) {
    const h2 = document.createElement("h2");
    h2.textContent = cabin.cabin_class || cabin.cabinClass || "cabin";
    root.appendChild(h2);
    (cabin.rows || []).forEach(function(row) {
      const line = document.createElement("div");
      line.className = "seat-row";
      const rn = document.createElement("span");
      rn.className = "rn";
      rn.textContent = row.row_number || row.rowNumber || "";
      line.appendChild(rn);
      let seats = row.seats || row.elements || [];
      if (!seats.length) {
        (row.sections || []).forEach(function(section) {
          seats = seats.concat(section.elements || []);
        });
      }
      seats.forEach(function(seat) {
        if (seat.type && seat.type !== "seat") return;
        const cell = document.createElement("span");
        const available = seat.available != null ? seat.available : (seat.available_services != null);
        cell.className = available ? "seat ok" : "seat no";
        cell.textContent = seat.designator || seat.id || seat.column || "";
        line.appendChild(cell);
      });
      root.appendChild(line);
    });
  });
};
""".strip()


_CANCEL_JS = """
window.__nitroWidgetRender = function(data) {
  const card = document.querySelector(".card");
  if (!card || !data) return;
  const status = data.status || "cancelled";
  const cid = data.id || data.cancellationId || "";
  const refund = data.refund_amount || data.refundAmount || "";
  const currency = data.refund_currency || data.refundCurrency || "";
  card.innerHTML = "";
  const h1 = document.createElement("h1");
  h1.textContent = "Booking cancelled";
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = status + " · " + cid;
  const p = document.createElement("p");
  p.textContent = data.message || "Booking cancelled.";
  const price = document.createElement("div");
  price.className = "price";
  price.textContent = "Refund " + (refund ? (refund + (currency ? " " + currency : "")) : "n/a");
  card.appendChild(h1);
  card.appendChild(meta);
  card.appendChild(p);
  card.appendChild(price);
};
""".strip()


def build_flight_search_results_widget(data: Any | None = None) -> str:
    return _page(
        "Flight search",
        _FLIGHT_CSS,
        render_body("flight-search-results", data),
        _FLIGHT_SEARCH_JS,
        data,
    )


def build_flight_details_widget(data: Any | None = None) -> str:
    return _page(
        "Flight details",
        _FLIGHT_CSS,
        render_body("flight-details", data),
        _FLIGHT_DETAILS_JS,
        data,
    )


def build_airport_search_widget(data: Any | None = None) -> str:
    return _page(
        "Airport search",
        _FLIGHT_CSS,
        render_body("airport-search", data),
        _AIRPORT_JS,
        data,
    )


def build_order_summary_widget(data: Any | None = None) -> str:
    return _page(
        "Order summary",
        _FLIGHT_CSS,
        render_body("order-summary", data),
        _ORDER_SUMMARY_JS,
        data,
    )


def build_seat_selection_widget(data: Any | None = None) -> str:
    return _page(
        "Seat map",
        _FLIGHT_CSS,
        render_body("seat-selection", data),
        _SEAT_JS,
        data,
    )


def build_order_cancellation_widget(data: Any | None = None) -> str:
    return _page(
        "Cancellation",
        _FLIGHT_CSS,
        render_body("order-cancellation", data),
        _CANCEL_JS,
        data,
    )
