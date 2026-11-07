"""Pizza list, map and shop widget templates (CSS and render JS)."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.route_templates_generic import _page
from nitrostack.widgets.views import render_body


_PIZZA_LIST_CSS = """
    :root { --ns-accent: #ea580c; --ns-accent-soft: #ffedd5; }
    .shop { min-width: 260px; max-width: 260px; scroll-snap-align: start; flex-shrink: 0; }
    .img { width: 100%; height: 120px; object-fit: cover; border-radius: 10px; margin-bottom: 8px; background: var(--ns-accent-soft); }
    .row { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .name { font-weight: 700; }
    .addr { color: var(--ns-muted); font-size: 0.85rem; margin-top: 4px; }
"""


_PIZZA_MAP_CSS = """
    :root { --ns-accent: #ea580c; }
    html, body { width: 100%; height: 560px; }
    body { position: relative; min-height: 560px; background: #e5e7eb; }
    .meta { position: absolute; top: 12px; left: 12px; z-index: 3; margin: 0;
      background: color-mix(in srgb, var(--ns-surface) 92%, transparent); border-radius: 8px; padding: 6px 10px;
      box-shadow: var(--ns-shadow); }
    .map { position: absolute; inset: 0; width: 100%; height: 560px; min-height: 560px; }
    .map-live { position: absolute; inset: 0; }
    .static-map { position: absolute; inset: 0; z-index: 1; width: 100%; height: 560px;
      object-fit: cover; transition: opacity .4s ease; }
    .static-map.is-ready { opacity: 0; pointer-events: none; }
    .mapboxgl-map { width: 100%; height: 100%; }
    .cards { position: absolute; left: 16px; right: 16px; bottom: 8px; z-index: 3; }
    .map-card { min-width: 220px; max-width: 220px; flex-shrink: 0; scroll-snap-align: start; }
    .map-card .name { font-weight: 700; }
    .map-card .muted { margin-top: 4px; }
    .map-card .rating { margin-top: 4px; }
"""


_PIZZA_SHOP_CSS = """
    :root { --ns-accent: #e11d48; --ns-accent-soft: #ffe4e6; }
    .card { background: var(--ns-surface); border-radius: var(--ns-radius); padding: 16px; border: 1px solid var(--ns-border); }
    .hero { width: 100%; max-height: 180px; object-fit: cover; border-radius: 10px; margin-bottom: 12px; }
    .chips { margin-top: 10px; }
    .chip { margin: 0 4px 4px 0; }
"""


_PIZZA_LIST_JS = """
window.__nitroWidgetRender = function(data) {
  const list = document.getElementById("list");
  const meta = document.getElementById("meta");
  if (!list || !meta) return;
  list.innerHTML = "";
  if (!data || !Array.isArray(data.shops)) {
    meta.textContent = "Waiting for show_pizza_list result.";
    return;
  }
  const state = (window.openai && window.openai.widgetState) || {};
  const sort = state.sort || "rating";
  const shops = data.shops.slice().sort(function(a, b) {
    if (sort === "name") return String(a.name || "").localeCompare(String(b.name || ""));
    return (b.rating || 0) - (a.rating || 0);
  });
  document.querySelectorAll("#sorts [data-sort]").forEach(function(btn) {
    btn.classList.toggle("is-on", btn.getAttribute("data-sort") === sort);
  });
  const total = data.totalShops ?? data.shops.length;
  meta.textContent = total + " shop" + (total === 1 ? "" : "s");
  if (!shops.length) {
    list.innerHTML = '<div class="empty">No shops match these filters.</div>';
    return;
  }
  shops.forEach(function(shop) {
    const el = document.createElement("article");
    el.className = "shop ns-card";
    el.setAttribute("data-call-tool", "show_pizza_shop");
    el.setAttribute("data-args", JSON.stringify({ shopId: shop.id || "" }));
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    const open = shop.openNow
      ? '<span class="badge open">Open</span>'
      : '<span class="badge closed">Closed</span>';
    const price = "$".repeat(shop.priceLevel || 1);
    let img = "";
    if (shop.image) {
      img = '<img class="img" src="' + shop.image + '" alt="" />';
    }
    el.innerHTML =
      img +
      '<div class="row"><span class="name"></span><span class="rating"></span></div>' +
      '<div class="addr"></div><div>' + open + ' <span class="badge price"></span></div>';
    el.querySelector(".name").textContent = shop.name || shop.id || "Shop";
    el.querySelector(".rating").textContent = shop.rating != null ? "★ " + shop.rating : "";
    el.querySelector(".addr").textContent = shop.address || "";
    el.querySelector(".price").textContent = price;
    list.appendChild(el);
  });
};
document.addEventListener("click", function(ev) {
  const btn = ev.target.closest("#sorts [data-sort]");
  if (!btn || !window.nitrostack) return;
  const next = Object.assign({}, (window.openai && window.openai.widgetState) || {}, {
    sort: btn.getAttribute("data-sort")
  });
  window.nitrostack.setWidgetState(next);
  if (typeof window.__nitroWidgetRender === "function" && window.nitrostack.readHostData) {
    window.__nitroWidgetRender(window.nitrostack.readHostData());
  }
});
""".strip()


_MAPBOX_HEAD = """
  <link rel="stylesheet" href="https://api.mapbox.com/mapbox-gl-js/v3.8.0/mapbox-gl.css" />
  <script src="https://api.mapbox.com/mapbox-gl-js/v3.8.0/mapbox-gl.js"></script>
""".strip()


def _pizza_map_js() -> str:
    import json

    from nitrostack.widgets.html_util import get_mapbox_token

    token = json.dumps(get_mapbox_token())
    return f"""
window.__NITRO_MAPBOX_TOKEN = {token};
window.__nitroWidgetRender = function(data) {{
  const meta = document.getElementById("meta");
  const mapEl = document.getElementById("map-live") || document.getElementById("map");
  const cards = document.getElementById("cards");
  const staticEl = document.getElementById("static-map");
  if (!mapEl) return;
  if (!data || !Array.isArray(data.shops) || !data.shops.length) {{
    if (meta) meta.textContent = "Waiting for show_pizza_map result.";
    return;
  }}
  const shops = data.shops.filter(function(s) {{ return Array.isArray(s.coords) && s.coords.length === 2; }});
  const sig = (data.filter || "all") + "|" + shops.map(function(s) {{
    return (s.id || "") + ":" + s.coords[0] + "," + s.coords[1];
  }}).join(";");
  if (window.__nitroMapSig === sig && window.__nitroMap) return;
  if (meta) meta.textContent = (data.totalShops ?? data.shops.length) + " shops · filter: " + (data.filter || "all");
  if (cards && !cards.querySelector(".map-card")) {{
    data.shops.forEach(function(shop) {{
      const card = document.createElement("article");
      card.className = "map-card ns-card";
      card.setAttribute("data-shop-id", shop.id || "");
      card.setAttribute("data-call-tool", "show_pizza_shop");
      card.setAttribute("data-args", JSON.stringify({{ shopId: shop.id || "" }}));
      card.setAttribute("role", "button");
      card.tabIndex = 0;
      card.innerHTML = '<div class="name"></div><div class="muted"></div><div class="rating"></div>';
      card.querySelector(".name").textContent = shop.name || shop.id || "Shop";
      card.querySelector(".muted").textContent = shop.address || "";
      card.querySelector(".rating").textContent = shop.rating != null ? "★ " + shop.rating : "";
      cards.appendChild(card);
    }});
  }}
  function revealLive() {{
    if (staticEl) staticEl.classList.add("is-ready");
  }}
  function mountMap(attempt) {{
    if (window.__nitroMapSig === sig && window.__nitroMap) return;
    if (!window.__NITRO_MAPBOX_TOKEN) {{
      if (meta) meta.textContent = "Set MAPBOX_TOKEN in .env to load the map.";
      if (mapEl) {{
        mapEl.innerHTML = '<div class="empty" style="padding:24px;position:relative;z-index:2">Set MAPBOX_TOKEN in .env (Mapbox public pk. token) to load the map.</div>';
      }}
      return;
    }}
    if (typeof mapboxgl === "undefined") {{
      if ((attempt || 0) < 80) {{
        setTimeout(function() {{ mountMap((attempt || 0) + 1); }}, 50);
      }}
      return;
    }}
    window.__nitroMapSig = sig;
    if (window.__nitroMap) {{
      window.__nitroMap.remove();
      window.__nitroMap = null;
    }}
    mapboxgl.accessToken = window.__NITRO_MAPBOX_TOKEN;
    const first = (shops[0] && shops[0].coords) || [-122.4194, 37.7749];
    const map = new mapboxgl.Map({{
      container: mapEl,
      style: "mapbox://styles/mapbox/streets-v12",
      center: first,
      zoom: 12,
      cooperativeGestures: false,
      fadeDuration: 0
    }});
    map.addControl(new mapboxgl.NavigationControl({{ showCompass: false }}), "top-right");
    map.addControl(new mapboxgl.FullscreenControl(), "top-right");
    shops.forEach(function(shop) {{
      const el = document.createElement("div");
      el.style.backgroundImage = "url(https://docs.mapbox.com/mapbox-gl-js/assets/custom_marker.png)";
      el.style.width = "30px";
      el.style.height = "40px";
      el.style.backgroundSize = "100%";
      el.style.cursor = "pointer";
      el.title = shop.name || shop.id || "";
      el.addEventListener("click", function() {{
        if (window.nitrostack) window.nitrostack.callTool("show_pizza_shop", {{ shopId: shop.id || "" }});
      }});
      new mapboxgl.Marker(el).setLngLat(shop.coords).addTo(map);
    }});
    if (shops.length > 1) {{
      const bounds = new mapboxgl.LngLatBounds();
      shops.forEach(function(s) {{ bounds.extend(s.coords); }});
      map.fitBounds(bounds, {{ padding: 60, maxZoom: 14, duration: 0 }});
    }}
    map.once("idle", function() {{
      try {{ map.resize(); }} catch (e) {{}}
      revealLive();
    }});
    window.__nitroMap = map;
  }}
  mountMap(0);
}};
""".strip()


_PIZZA_SHOP_JS = """
window.__nitroWidgetRender = function(data) {
  const shop = data && (data.shop || data);
  const hero = document.getElementById("hero");
  if (!shop || !shop.name) {
    const desc = document.getElementById("desc");
    if (desc) desc.textContent = "Waiting for show_pizza_shop result.";
    return;
  }
  if (shop.image && hero) {
    hero.src = shop.image;
    hero.style.display = "block";
  } else if (hero) {
    hero.style.display = "none";
  }
  document.getElementById("name").textContent = shop.name;
  document.getElementById("rating").textContent =
    shop.rating != null ? "★ " + shop.rating + " (" + (shop.reviews || 0) + " reviews)" : "";
  document.getElementById("desc").textContent = shop.description || "";
  document.getElementById("addr").textContent = shop.address || "";
  const hours = shop.hours ? (shop.hours.open + " – " + shop.hours.close) : "";
  document.getElementById("hours").textContent = hours ? "Hours: " + hours : "";
  document.getElementById("phone").textContent = shop.phone || "";
  const chips = document.getElementById("chips");
  chips.innerHTML = "";
  (shop.specialties || []).forEach(function(item) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    chips.appendChild(chip);
  });
  const actions = document.getElementById("actions");
  if (actions) {
    actions.innerHTML = "";
    function addLink(label, url, primary) {
      if (!url) return;
      const a = document.createElement("a");
      a.className = primary ? "ns-btn ns-btn-primary" : "ns-btn";
      a.setAttribute("data-open-link", url);
      a.href = url;
      a.textContent = label;
      actions.appendChild(a);
    }
    let maps = "";
    if (Array.isArray(shop.coords) && shop.coords.length === 2) {
      maps = "https://www.google.com/maps/search/?api=1&query=" + shop.coords[1] + "," + shop.coords[0];
    } else if (shop.address) {
      maps = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(shop.address);
    }
    addLink("Maps", maps, false);
    if (shop.phone) {
      const tel = String(shop.phone).replace(/[^0-9+]/g, "");
      if (tel) addLink("Call", "tel:" + tel, false);
    }
    addLink("Website", shop.website || "", true);
  }
};
""".strip()


def build_pizza_list_widget(data: Any | None = None) -> str:
    return _page("Pizza shops", _PIZZA_LIST_CSS, render_body("pizza-list", data), _PIZZA_LIST_JS, data)


def build_pizza_map_widget(data: Any | None = None) -> str:
    return _page(
        "Pizza map",
        _PIZZA_MAP_CSS,
        render_body("pizza-map", data),
        _pizza_map_js(),
        data,
        extra_head=_MAPBOX_HEAD,
        flush=True,
    )


def build_pizza_shop_widget(data: Any | None = None) -> str:
    return _page("Pizza shop", _PIZZA_SHOP_CSS, render_body("pizza-shop", data), _PIZZA_SHOP_JS, data)
