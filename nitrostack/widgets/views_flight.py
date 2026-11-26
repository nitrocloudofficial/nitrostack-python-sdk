"""Flight search, details, airport, order, seat and cancellation widget bodies."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.html_util import (
    as_dict,
    as_list,
    esc,
    format_duration,
    money,
    pick,
    place_code,
)
from nitrostack.widgets.ui import call_attrs


def _offer_slice_html(sl: dict) -> str:
    origin = place_code(sl.get("origin"))
    dest = place_code(sl.get("destination"))
    duration = format_duration(sl.get("duration"))
    segs = as_list(sl.get("segments"))
    first = as_dict(segs[0] if segs else {})
    last = as_dict(segs[-1] if segs else first)
    dep = pick(first, "departing_at", "departingAt", "departureTime", default="") or pick(
        sl, "departing_at", "departingAt", "departureTime", default=""
    )
    arr = pick(last, "arriving_at", "arrivingAt", "arrivalTime", default="") or pick(
        sl, "arriving_at", "arrivingAt", "arrivalTime", default=""
    )
    carrier = first.get("marketing_carrier") or first.get("airline") or sl.get("airline")
    if isinstance(carrier, str):
        airline = carrier
        flight_no = pick(first, "marketing_carrier_flight_number", "flightNumber", default="") or pick(
            sl, "flightNumber", "marketing_carrier_flight_number", default=""
        )
    else:
        carrier = as_dict(carrier)
        airline = pick(carrier, "name", default="") or pick(first, "airline", default="") or pick(sl, "airline", default="")
        flight_no = pick(carrier, "flight_number", "flightNumber", default="") or pick(
            first, "marketing_carrier_flight_number", "flightNumber", default=""
        ) or pick(sl, "flightNumber", default="")
    return (
        f'<div class="slice"><strong>{esc(origin)} → {esc(dest)}</strong>'
        f'<div class="muted">{esc(dep)} – {esc(arr)} · {esc(duration)}</div>'
        f'<div class="muted">{esc(airline)} {esc(flight_no)}</div></div>'
    )


def _offer_itinerary_html(offer: dict) -> str:
    slices = as_list(offer.get("slices"))
    if slices:
        return "".join(_offer_slice_html(as_dict(s)) for s in slices)
    parts = []
    outbound = offer.get("outbound")
    if outbound:
        parts.append(_offer_slice_html(as_dict(outbound)))
    ret = offer.get("return")
    if ret:
        parts.append(_offer_slice_html(as_dict(ret)))
    return "".join(parts) or _offer_slice_html(offer)


def flight_search_results_body(data: Any | None) -> str:
    payload = as_dict(data)
    offers = as_list(payload.get("offers") or payload.get("results"))
    if data is None:
        return (
            "<h1>Flight search</h1>"
            '<div class="meta" id="meta">Waiting for search_flights result.</div>'
            '<div id="list"></div><div id="root"></div>'
        )
    params = as_dict(payload.get("searchParams"))
    total = payload.get("totalOffers")
    if total is None:
        total = len(offers)
    if params.get("origin") and params.get("destination"):
        meta = f"{params.get('origin')} → {params.get('destination')} · {total} offer" + ("" if total == 1 else "s")
    else:
        meta = f"{total} offer" + ("" if total == 1 else "s")
    cards = []
    for offer in offers:
        offer = as_dict(offer)
        amount = pick(offer, "total_amount", "totalAmount", default="")
        currency = pick(offer, "total_currency", "totalCurrency", default="")
        slice_html = _offer_itinerary_html(offer)
        offer_id = offer.get("id") or ""
        cards.append(
            f'<article class="offer ns-card" {call_attrs("get_flight_details", {"offerId": offer_id})}>'
            f'<div class="row"><span class="price">{esc(money(amount, currency))}</span>'
            f'<span class="id">{esc(offer_id)}</span></div>{slice_html}</article>'
        )
    if not cards:
        cards.append('<div class="empty">No flight offers.</div>')
    return (
        "<h1>Flight search</h1>"
        f'<div class="meta" id="meta">{esc(meta)}</div>'
        f'<div id="list">{"".join(cards)}</div><div id="root"></div>'
    )


def flight_details_body(data: Any | None) -> str:
    payload = as_dict(data)
    if data is None or not payload:
        return (
            "<h1>Flight details</h1>"
            '<div class="meta" id="meta">Waiting for get_flight_details result.</div>'
            '<div id="root"></div>'
        )
    amount = pick(payload, "total_amount", "totalAmount", default="")
    currency = pick(payload, "total_currency", "totalCurrency", default="")
    slices = as_list(payload.get("slices"))
    slice_html = "".join(_offer_slice_html(as_dict(s)) for s in slices)
    offer_id = payload.get("id") or ""
    seats = ""
    if offer_id:
        seats = (
            f'<div class="ns-actions"><button type="button" class="ns-btn ns-btn-primary" '
            f'{call_attrs("get_seat_map", {"offerId": offer_id})}>Seat map</button></div>'
        )
    return (
        "<h1>Flight details</h1>"
        f'<div class="meta" id="meta">Offer {esc(offer_id)} · {esc(money(amount, currency))}</div>'
        f'<div id="root">{slice_html}{seats}</div>'
    )


def airport_search_body(data: Any | None) -> str:
    payload = as_dict(data)
    results = as_list(payload.get("results") or payload.get("airports") or payload.get("places"))
    query = payload.get("query") or ""
    if data is None:
        return (
            "<h1>Airport search</h1>"
            '<div class="meta" id="meta">Waiting for search_airports result.</div>'
            '<div id="list"></div><div id="root"></div>'
        )
    rows = []
    for item in results:
        item = as_dict(item)
        code = pick(item, "iata_code", "iataCode", default="")
        name = pick(item, "name", default="")
        city = pick(item, "city_name", "cityName", "city", default="")
        kind = pick(item, "type", default="airport")
        rows.append(
            f'<div class="row"><span class="code">{esc(code)}</span>'
            f'<div><div class="name">{esc(name)}</div>'
            f'<div class="muted">{esc(city)} · {esc(kind)}</div></div></div>'
        )
    if not rows:
        rows.append('<div class="empty">No airports found.</div>')
    return (
        "<h1>Airport search</h1>"
        f'<div class="meta" id="meta">Query: {esc(query)} · {len(results)} result(s)</div>'
        f'<div id="list">{"".join(rows)}</div><div id="root"></div>'
    )


def order_summary_body(data: Any | None) -> str:
    payload = as_dict(data)
    if data is None or not payload:
        return (
            "<h1>Order summary</h1>"
            '<div class="meta" id="meta">Waiting for order result.</div>'
            '<div id="root"></div>'
        )
    status = str(pick(payload, "status", default="held"))
    ref = pick(payload, "booking_reference", "bookingReference", default="")
    amount = pick(payload, "total_amount", "totalAmount", default="")
    currency = pick(payload, "total_currency", "totalCurrency", default="")
    passengers = as_list(payload.get("passengers"))
    pax = []
    for p in passengers:
        p = as_dict(p)
        name = pick(p, "name", default="") or " ".join(
            str(x) for x in (p.get("given_name") or p.get("givenName"), p.get("family_name") or p.get("familyName")) if x
        )
        pax.append(f'<div class="muted">{esc(name or p.get("id"))}</div>')
    slices = "".join(_offer_slice_html(as_dict(s)) for s in as_list(payload.get("slices")))
    order_id = payload.get("id") or payload.get("orderId") or ""
    cancel = ""
    if order_id and status.lower() not in {"cancelled", "canceled"}:
        cancel = (
            f'<div class="ns-actions"><button type="button" class="ns-btn" '
            f'{call_attrs("cancel_order", {"orderId": order_id})}>Cancel order</button></div>'
        )
    return (
        '<div class="card ns-surface">'
        f'<h1>Order {esc(status)}</h1>'
        f'<div class="meta">Ref {esc(ref)} · {esc(order_id)}</div>'
        f'<div class="price">{esc(money(amount, currency))}</div>'
        f'<h2>Passengers</h2>{"".join(pax) or "<div class=muted>None listed</div>"}'
        f'<h2>Itinerary</h2>{slices}'
        f"{cancel}"
        "</div>"
        '<div id="root"></div>'
    )


def _seat_cells(cabin: dict) -> str:
    rows_html = []
    rows = as_list(cabin.get("rows"))
    if rows:
        for row in rows:
            row = as_dict(row)
            seats = as_list(row.get("seats") or row.get("elements"))
            if not seats:
                for section in as_list(row.get("sections")):
                    seats.extend(as_list(as_dict(section).get("elements")))
            cells = []
            for seat in seats:
                seat = as_dict(seat)
                if seat.get("type") and seat.get("type") != "seat":
                    continue
                designator = pick(seat, "designator", "id", "column", default="")
                available = seat.get("available")
                if available is None:
                    available = bool(seat.get("available_services") is not None)
                cls = "seat ok" if available else "seat no"
                cells.append(f'<span class="{cls}">{esc(designator)}</span>')
            rows_html.append(
                f'<div class="seat-row"><span class="rn">{esc(row.get("row_number") or row.get("rowNumber") or "")}</span>'
                f"{''.join(cells)}</div>"
            )
    return "".join(rows_html)


def seat_selection_body(data: Any | None) -> str:
    payload = as_dict(data)
    cabins = as_list(payload.get("cabins"))
    if data is None:
        return (
            "<h1>Seat map</h1>"
            '<div class="meta" id="meta">Waiting for get_seat_map result.</div>'
            '<div id="root"></div>'
        )
    blocks = []
    for cabin in cabins:
        cabin = as_dict(cabin)
        klass = pick(cabin, "cabin_class", "cabinClass", default="cabin")
        blocks.append(f'<h2>{esc(klass)}</h2>{_seat_cells(cabin)}')
    if not blocks:
        blocks.append('<div class="empty">No seat map data.</div>')
    return (
        "<h1>Seat map</h1>"
        f'<div class="meta" id="meta">Offer {esc(payload.get("offerId") or payload.get("offer_id"))}</div>'
        f'<div id="root">{"".join(blocks)}</div>'
    )


def order_cancellation_body(data: Any | None) -> str:
    payload = as_dict(data)
    if data is None or not payload:
        return (
            "<h1>Cancellation</h1>"
            '<div class="meta" id="meta">Waiting for cancel_order result.</div>'
            '<div id="root"></div>'
        )
    refund = pick(payload, "refund_amount", "refundAmount", default="")
    currency = pick(payload, "refund_currency", "refundCurrency", default="")
    status = pick(payload, "status", default="cancelled")
    msg = pick(payload, "message", default="Booking cancelled.")
    cid = pick(payload, "id", "cancellationId", default="")
    return (
        '<div class="card">'
        "<h1>Booking cancelled</h1>"
        f'<div class="meta">{esc(status)} · {esc(cid)}</div>'
        f'<p>{esc(msg)}</p>'
        f'<div class="price">Refund {esc(money(refund, currency) or "n/a")}</div>'
        "</div>"
        '<div id="root"></div>'
    )
