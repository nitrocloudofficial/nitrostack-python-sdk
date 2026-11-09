"""Pizza list, map and shop widget bodies."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.html_util import (
    as_dict,
    as_list,
    esc,
    mapbox_static_url,
)
from nitrostack.widgets.ui import action_row, call_attrs, maps_url
from nitrostack.widgets.views_common import _safe_int


def pizza_list_body(data: Any | None) -> str:
    payload = as_dict(data)
    shops = as_list(payload.get("shops"))
    total = payload.get("totalShops")
    if total is None:
        total = len(shops)
    if data is None:
        meta = "Waiting for show_pizza_list result."
        cards = ""
    elif not shops:
        meta = f"{total} shops"
        cards = '<div class="empty">No shops match these filters.</div>'
    else:
        meta = f"{total} shop" + ("" if total == 1 else "s")
        parts = []
        for shop in shops:
            shop = as_dict(shop)
            open_cls = "open" if shop.get("openNow") else "closed"
            open_label = "Open" if shop.get("openNow") else "Closed"
            price = "$" * _safe_int(shop.get("priceLevel"), 1, minimum=1, maximum=4)
            img = ""
            if shop.get("image"):
                img = f'<img class="img" src="{esc(shop["image"])}" alt="" />'
            rating = f"★ {esc(shop.get('rating'))}" if shop.get("rating") is not None else ""
            shop_id = shop.get("id") or ""
            parts.append(
                f'<article class="shop ns-card" {call_attrs("show_pizza_shop", {"shopId": shop_id})}>'
                f"{img}"
                f'<div class="row"><span class="name">{esc(shop.get("name") or shop_id or "Shop")}</span>'
                f'<span class="rating">{rating}</span></div>'
                f'<div class="addr">{esc(shop.get("address"))}</div>'
                f'<div><span class="badge {open_cls}">{open_label}</span> '
                f'<span class="badge">{esc(price)}</span></div>'
                "</article>"
            )
        cards = "".join(parts)
    return (
        "<h1>Pizza shops</h1>"
        f'<div class="meta" id="meta">{esc(meta)}</div>'
        '<div class="sorts" id="sorts">'
        '<button type="button" data-sort="rating" class="is-on">Rating</button>'
        '<button type="button" data-sort="name">Name</button>'
        "</div>"
        f'<div class="scroller" id="list">{cards}</div>'
        '<div id="root"></div>'
    )


def _pizza_map_card(shop: dict) -> str:
    name = shop.get("name") or shop.get("id") or "Shop"
    shop_id = shop.get("id") or ""
    rating = f"★ {esc(shop.get('rating'))}" if shop.get("rating") is not None else ""
    return (
        f'<article class="map-card ns-card" data-shop-id="{esc(shop_id)}" '
        f'{call_attrs("show_pizza_shop", {"shopId": shop_id})}>'
        f'<div class="name">{esc(name)}</div>'
        f'<div class="muted">{esc(shop.get("address"))}</div>'
        f'<div class="rating">{rating}</div></article>'
    )


def pizza_map_body(data: Any | None) -> str:
    payload = as_dict(data)
    shops = as_list(payload.get("shops"))
    static = ""
    if data is None or not shops:
        meta = "Waiting for show_pizza_map result."
        cards = ""
    else:
        filt = payload.get("filter") or "all"
        total = payload.get("totalShops")
        if total is None:
            total = len(shops)
        meta = f"{total} shops · filter: {filt}"
        cards = "".join(_pizza_map_card(as_dict(shop)) for shop in shops)
        static_src = mapbox_static_url(shops)
        static = (
            f'<img class="static-map" id="static-map" alt="Pizza shop map" '
            f'src="{esc(static_src)}" />'
            if static_src
            else ""
        )
    return (
        f'<div class="meta" id="meta">{esc(meta)}</div>'
        f'<div class="map" id="map" data-nitro-needs-client="1">'
        f'<div class="map-live" id="map-live"></div>{static}</div>'
        f'<div class="cards" id="cards">{cards}</div>'
        '<div id="root"></div>'
    )


def pizza_shop_body(data: Any | None) -> str:
    payload = as_dict(data)
    shop = as_dict(payload.get("shop") or payload if data is not None else {})
    if data is None or not shop.get("name"):
        return (
            '<div class="card">'
            '<img class="hero" id="hero" alt="" style="display:none" />'
            '<h1 id="name">Pizza shop</h1>'
            '<div class="rating" id="rating"></div>'
            '<p class="muted" id="desc">Waiting for show_pizza_shop result.</p>'
            '<p class="muted" id="addr"></p>'
            '<p class="muted" id="hours"></p>'
            '<p class="muted" id="phone"></p>'
            '<div class="chips" id="chips"></div>'
            '<div class="ns-actions" id="actions"></div>'
            "</div>"
            '<div id="root"></div>'
        )
    img = ""
    if shop.get("image"):
        img = f'<img class="hero" id="hero" src="{esc(shop["image"])}" alt="" />'
    else:
        img = '<img class="hero" id="hero" alt="" style="display:none" />'
    rating = ""
    if shop.get("rating") is not None:
        rating = f"★ {esc(shop.get('rating'))} ({esc(shop.get('reviews') or 0)} reviews)"
    hours = as_dict(shop.get("hours"))
    hours_text = ""
    if hours.get("open") or hours.get("close"):
        hours_text = f"Hours: {esc(hours.get('open'))} – {esc(hours.get('close'))}"
    chips = "".join(
        f'<span class="chip">{esc(item)}</span>' for item in as_list(shop.get("specialties"))
    )
    actions = action_row(maps=maps_url(shop), phone=str(shop.get("phone") or ""), website=str(shop.get("website") or ""))
    return (
        '<div class="card ns-surface">'
        f"{img}"
        f'<h1 id="name">{esc(shop.get("name"))}</h1>'
        f'<div class="rating" id="rating">{rating}</div>'
        f'<p class="muted" id="desc">{esc(shop.get("description"))}</p>'
        f'<p class="muted" id="addr">{esc(shop.get("address"))}</p>'
        f'<p class="muted" id="hours">{hours_text}</p>'
        f'<p class="muted" id="phone">{esc(shop.get("phone"))}</p>'
        f'<div class="chips" id="chips">{chips}</div>'
        f"{actions}"
        "</div>"
        '<div id="root"></div>'
    )
