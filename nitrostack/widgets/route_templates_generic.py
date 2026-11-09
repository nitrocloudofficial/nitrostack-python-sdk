"""Page wrapper plus the generic, missing, calculator, card, table and chart widget templates."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.host_bridge import wrap_widget_page
from nitrostack.widgets.views import missing_body, render_body


def _generic_render_js() -> str:
    return """
window.__nitroWidgetRender = function(data) {
  const root = document.getElementById("root");
  const meta = document.getElementById("meta");
  if (!root) return;
  if (!data) {
    if (meta) meta.textContent = "Waiting for tool result (host injects tool output).";
    return;
  }
  if (meta) meta.textContent = "Tool output";
  root.innerHTML = "";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(data, null, 2);
  root.appendChild(pre);
};
""".strip()


_CALC_CSS = """
    :root { --ns-accent: #15803d; }
    .card { background: var(--ns-surface); border: 1px solid var(--ns-border); border-radius: var(--ns-radius); padding: 20px; text-align: center; }
    .expr { font-size: 0.95rem; color: var(--ns-muted); margin-bottom: 8px; }
    .result { font-size: 2rem; font-weight: 800; color: var(--ns-accent); }
"""


_CARD_CSS = """
    .card { background: var(--ns-surface); border-radius: var(--ns-radius); padding: 20px; box-shadow: var(--ns-shadow); max-width: 320px; border: 1px solid var(--ns-border); }
    .card h2 { margin: 0 0 8px; font-size: 1.25rem; }
    .price { font-size: 1.5rem; }
    .desc { color: var(--ns-muted); margin-top: 8px; }
"""


_TABLE_CSS = """
    table { border-collapse: collapse; width: 100%; max-width: 480px; background: var(--ns-surface); border-radius: 10px; overflow: hidden; }
    th, td { border: 1px solid var(--ns-border); padding: 8px 12px; text-align: left; }
    th { background: var(--ns-accent-soft); }
"""


_CHART_CSS = """
    .chart { display: flex; align-items: flex-end; gap: 8px; height: 160px; }
    .bar-wrap { flex: 1; display: flex; flex-direction: column; align-items: center; }
    .bar { width: 100%; background: var(--ns-accent, #3b82f6); border-radius: 4px 4px 0 0; min-height: 4px; }
    .label { font-size: 12px; margin-top: 4px; color: var(--ns-muted); }
"""


_GENERIC_CSS = """
    pre { background: var(--ns-surface); border: 1px solid var(--ns-border); border-radius: 8px; padding: 12px; overflow: auto; }
"""


_MISSING_CSS = """
    :root { --ns-bg: #fef2f2; --ns-text: #7f1d1d; }
    .meta { font-size: 0.9rem; line-height: 1.5; }
"""


_CALC_JS = """
window.__nitroWidgetRender = function(data) {
  if (!data) return;
  const expr = document.getElementById("expr");
  const result = document.getElementById("result");
  if (data.expression) expr.textContent = data.expression;
  if (data.result != null) result.textContent = String(data.result);
  else if (data.value != null) result.textContent = String(data.value);
};
""".strip()


_CARD_JS = """
window.__nitroWidgetRender = function(data) {
  if (!data) return;
  const name = document.getElementById("name");
  const price = document.getElementById("price");
  const desc = document.getElementById("desc");
  if (name) name.textContent = data.name || "—";
  if (price) price.textContent = data.price != null ? "$" + Number(data.price).toFixed(2) : "—";
  if (desc) desc.textContent = data.description || "";
};
""".strip()


_TABLE_JS = """
window.__nitroWidgetRender = function(data) {
  if (!data || !Array.isArray(data.rows)) return;
  const cols = data.columns || Object.keys(data.rows[0] || {});
  const header = document.getElementById("header");
  const body = document.getElementById("body");
  if (!header || !body) return;
  header.innerHTML = "";
  body.innerHTML = "";
  cols.forEach(function(c) {
    const th = document.createElement("th");
    th.textContent = c;
    header.appendChild(th);
  });
  data.rows.forEach(function(row) {
    const tr = document.createElement("tr");
    cols.forEach(function(c) {
      const td = document.createElement("td");
      td.textContent = row[c] ?? "";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
};
""".strip()


_CHART_JS = """
window.__nitroWidgetRender = function(data) {
  if (!data) return;
  const title = document.getElementById("title");
  const chart = document.getElementById("chart");
  if (title) title.textContent = data.title || "Chart";
  if (!chart) return;
  chart.innerHTML = "";
  const items = data.items || [];
  const max = Math.max.apply(null, items.map(function(i) { return i.value || 0; }).concat([1]));
  items.forEach(function(item) {
    const wrap = document.createElement("div");
    wrap.className = "bar-wrap";
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = (((item.value || 0) / max) * 140) + "px";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label || "";
    wrap.appendChild(bar);
    wrap.appendChild(label);
    chart.appendChild(wrap);
  });
};
""".strip()


def _page(
    title: str,
    styles: str,
    body: str,
    render_js: str,
    data: Any | None,
    extra_head: str = "",
    flush: bool = False,
    chrome: bool = True,
) -> str:
    return wrap_widget_page(
        title=title,
        styles=styles,
        body=body,
        render_js=render_js,
        data=data,
        extra_head=extra_head,
        flush=flush,
        chrome=chrome,
    )


def build_generic_widget(route: str, data: Any | None = None) -> str:
    return _page(route, _GENERIC_CSS, render_body(route, data), _generic_render_js(), data)


def build_missing_widget(route: str, data: Any | None = None) -> str:
    return _page(
        f"Missing widget: {route}",
        _MISSING_CSS,
        missing_body(route),
        _generic_render_js(),
        data,
    )


def build_calculator_result_widget(data: Any | None = None) -> str:
    return _page(
        "Calculator result",
        _CALC_CSS,
        render_body("calculator-result", data),
        _CALC_JS,
        data,
    )


def build_card_widget(data: Any | None = None) -> str:
    return _page("Product Card", _CARD_CSS, render_body("card", data), _CARD_JS, data)


def build_table_widget(data: Any | None = None) -> str:
    return _page("Data Table", _TABLE_CSS, render_body("table", data), _TABLE_JS, data)


def build_chart_widget(data: Any | None = None) -> str:
    return _page("Bar Chart", _CHART_CSS, render_body("chart", data), _CHART_JS, data)
