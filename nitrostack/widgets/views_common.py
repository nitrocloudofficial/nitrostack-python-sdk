"""Shared view helpers and the generic, calculator, card, table and chart widget bodies."""

from __future__ import annotations

from typing import Any

from nitrostack.widgets.html_util import as_dict, as_list, esc


def _safe_int(
    value: Any,
    default: int = 0,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        # OverflowError covers infinities: `json.loads("1e400")` yields `inf`, and
        # `int(float("inf"))` raises. NaN already surfaces as ValueError.
        n = int(float(value))
    except (TypeError, ValueError, OverflowError):
        n = default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n


def calculator_result_body(data: Any | None) -> str:
    payload = as_dict(data)
    expr = payload.get("expression") or "—"
    result = payload.get("result")
    if result is None:
        result = payload.get("value")
    result_text = "—" if result is None else str(result)
    return (
        '<div class="card">'
        f'<div class="expr" id="expr">{esc(expr)}</div>'
        f'<div class="result" id="result">{esc(result_text)}</div>'
        "</div>"
        '<div id="root"></div>'
        '<div class="meta" id="meta"></div>'
    )


def card_body(data: Any | None) -> str:
    payload = as_dict(data)
    name = payload.get("name") or "—"
    price = payload.get("price")
    price_text = f"${float(price):.2f}" if isinstance(price, (int, float)) else (str(price) if price else "—")
    desc = payload.get("description") or ""
    return (
        '<div class="card">'
        f'<h2 id="name">{esc(name)}</h2>'
        f'<div class="price" id="price">{esc(price_text)}</div>'
        f'<p class="desc" id="desc">{esc(desc)}</p>'
        "</div>"
    )


def table_body(data: Any | None) -> str:
    payload = as_dict(data)
    rows = as_list(payload.get("rows"))
    cols = as_list(payload.get("columns"))
    if not cols and rows:
        cols = list(as_dict(rows[0]).keys())
    header = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body_rows = []
    for row in rows:
        row = as_dict(row)
        cells = "".join(f"<td>{esc(row.get(c, ''))}</td>" for c in cols)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        "<table>"
        f'<thead><tr id="header">{header}</tr></thead>'
        f'<tbody id="body">{"".join(body_rows)}</tbody>'
        "</table>"
    )


def chart_body(data: Any | None) -> str:
    payload = as_dict(data)
    title = payload.get("title") or "Chart"
    items = as_list(payload.get("items"))
    values = [_safe_float(as_dict(i).get("value"), 0.0) for i in items]
    max_v = max(values or [1], default=1) or 1
    bars = []
    for item, value in zip(items, values):
        item = as_dict(item)
        height = (value / max_v) * 140
        bars.append(
            '<div class="bar-wrap">'
            f'<div class="bar" style="height:{height:.1f}px"></div>'
            f'<div class="label">{esc(item.get("label"))}</div>'
            "</div>"
        )
    return f'<h3 id="title">{esc(title)}</h3><div class="chart" id="chart">{"".join(bars)}</div>'


def generic_body(route: str, data: Any | None) -> str:
    import json

    if data is None:
        preview = "Waiting for host to send tool output…"
    else:
        preview = json.dumps(data, indent=2, default=str)
    return (
        f"<h1>{esc(route)}</h1>"
        '<div class="meta" id="meta">Tool output</div>'
        f'<pre id="root">{esc(preview)}</pre>'
    )


def missing_body(route: str) -> str:
    return (
        "<h1>Widget template missing</h1>"
        f'<div class="meta" id="meta">No HTML found for route <strong>{esc(route)}</strong>. '
        f"Create <code>widgets/out/{esc(route)}.html</code> or run <code>nitrostack-py init</code> again.</div>"
        '<div id="root"></div>'
    )
