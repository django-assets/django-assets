"""SVG chart geometry (ALLOWED arithmetic zone — pixels, not money).

These inclusion tags turn finished report series from
django_assets.trades.reports into pixel coordinates for the SVG chart
includes under templates/optiontracker/charts/. Floats are fine here:
every number produced is a coordinate, a bar height, or an axis tick —
chart scaling explicitly allowed by the build rules and allowlisted by
scripts/check_app_thinness.py. Dollar LABELS on bars/legends re-use the
library-provided Decimals untouched; axis tick captions are derived
scale marks, part of chart rendering.
"""

import datetime

from django import template

register = template.Library()

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _tick_label(value: float) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{sign}${magnitude / 1000:,.1f}K"
    return f"{sign}${magnitude:,.0f}"


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if high <= low:
        high = low + 1.0
    span = (high - low) / max(count - 1, 1)
    ticks = [low + span * i for i in range(count)]
    return ticks


@register.inclusion_tag("optiontracker/charts/bar_chart.html")
def monthly_bar_chart(monthly_profit: dict) -> dict:
    """Vertical bars from PerformanceStats.monthly_profit."""
    width, height = 860.0, 300.0
    pad_left, pad_right, pad_top, pad_bottom = 64.0, 16.0, 16.0, 28.0
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    items = sorted(monthly_profit.items())
    values = [float(amount) for _month, amount in items]
    low = min(0.0, *values) if values else 0.0
    high = max(0.0, *values) if values else 1.0
    if high == low:
        high = low + 1.0

    def y_of(value: float) -> float:
        return pad_top + (high - value) / (high - low) * plot_h

    bars = []
    slot = plot_w / max(len(items), 1)
    bar_w = min(46.0, slot * 0.6)
    for index, (month, amount) in enumerate(items):
        value = float(amount)
        top = y_of(max(value, 0.0))
        bottom = y_of(min(value, 0.0))
        bars.append(
            {
                "x": pad_left + slot * index + (slot - bar_w) / 2,
                "y": top,
                "w": bar_w,
                "h": max(bottom - top, 1.0),
                "negative": value < 0,
                "label": _MONTHS[month.month - 1],
                "label_x": pad_left + slot * index + slot / 2,
                "amount": amount,  # library Decimal, formatted by tracker_format
            }
        )
    ticks = [{"y": y_of(value), "label": _tick_label(value)} for value in _nice_ticks(low, high)]
    zero_y = y_of(0.0)
    return {
        "width": width,
        "height": height,
        "bars": bars,
        "ticks": ticks,
        "zero_y": zero_y,
        "pad_left": pad_left,
        "tick_x": pad_left - 8.0,
        "plot_right": width - pad_right,
        "label_y": height - 8.0,
    }


@register.inclusion_tag("optiontracker/charts/line_chart.html")
def cumulative_line_chart(daily_cumulative: list, account_values: list | None = None) -> dict:
    """Two report series on one time axis: PerformanceStats.daily_cumulative
    (line + area) overlaid with account_value_series() [(date, Decimal)]."""
    width, height = 860.0, 300.0
    pad_left, pad_right, pad_top, pad_bottom = 64.0, 16.0, 16.0, 28.0
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    account_values = account_values or []
    if not daily_cumulative and not account_values:
        return {"width": width, "height": height, "empty": True}

    all_series = [series for series in (daily_cumulative, account_values) if series]
    all_days = [day.toordinal() for series in all_series for day, _v in series]
    all_values = [float(value) for series in all_series for _day, value in series]
    x_low, x_high = min(all_days), max(all_days)
    if x_high == x_low:
        x_high = x_low + 1
    low = min(0.0, *all_values)
    high = max(0.0, *all_values)
    if high == low:
        high = low + 1.0

    def x_of(ordinal: int) -> float:
        return pad_left + (ordinal - x_low) / (x_high - x_low) * plot_w

    def y_of(value: float) -> float:
        return pad_top + (high - value) / (high - low) * plot_h

    def polyline(series: list) -> str:
        return " ".join(
            f"{x_of(day.toordinal()):.1f},{y_of(float(value)):.1f}" for day, value in series
        )

    line = area = None
    if daily_cumulative:
        points = [(x_of(day.toordinal()), y_of(float(value))) for day, value in daily_cumulative]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        baseline = y_of(0.0)
        area = f"{points[0][0]:.1f},{baseline:.1f} " + line + f" {points[-1][0]:.1f},{baseline:.1f}"
    value_line = polyline(account_values) if account_values else None
    ticks = [{"y": y_of(value), "label": _tick_label(value)} for value in _nice_ticks(low, high)]
    x_tick_count = min(6, len(set(all_days)))
    x_ticks = []
    for i in range(x_tick_count):
        ordinal = x_low + (x_high - x_low) * i // max(x_tick_count - 1, 1)
        day = datetime.date.fromordinal(ordinal)
        x_ticks.append({"x": x_of(ordinal), "label": f"{_MONTHS[day.month - 1]} {day.day}"})
    return {
        "width": width,
        "height": height,
        "empty": False,
        "line": line,
        "area": area,
        "value_line": value_line,
        "ticks": ticks,
        "x_ticks": x_ticks,
        "pad_left": pad_left,
        "tick_x": pad_left - 8.0,
        "plot_right": width - pad_right,
        "label_y": height - 8.0,
    }


@register.inclusion_tag("optiontracker/charts/flow_chart.html")
def pnl_flow_chart(summary) -> dict:
    """Sankey-ish three-column flow from pnl_flow_summary()'s FlowSummary.

    Each FlowRow is one edge (symbol -> put/call -> gain/loss); node
    heights and ribbon widths scale with abs(realized_pnl). Node labels
    carry the library-provided per-node totals (by_symbol / by_right /
    by_outcome) and share_of_total() fractions, formatted downstream by
    tracker_format filters.
    """
    rows = summary.rows
    width, height = 960.0, 560.0
    node_w, pad_y, gap = 14.0, 24.0, 14.0
    col_x = [180.0, 483.0, 786.0]
    right_labels = {"P": "Put", "C": "Call", "mixed": "Mixed"}
    outcome_labels = {"gain": "Gain", "loss": "Loss"}

    def build_nodes(keys: list, of_row, amount_of_key) -> dict:
        nodes: dict = {}
        for row in rows:
            key = of_row(row)
            node = nodes.setdefault(key, {"key": key, "weight": 0.0, "rows": []})
            node["weight"] += abs(float(row.realized_pnl))
            node["rows"].append(row)
        for key, node in nodes.items():
            amount = amount_of_key(key)  # library-computed node total
            node["amount"] = amount
            node["share"] = summary.share_of_total(amount) if amount is not None else None
        return {key: nodes[key] for key in keys if key in nodes}

    by_symbol_code = {inst.code: amount for inst, amount in summary.by_symbol.items()}
    symbol_order = list(dict.fromkeys(row.underlying.code for row in rows))
    columns = [
        build_nodes(symbol_order, lambda row: row.underlying.code, by_symbol_code.get),
        build_nodes(["P", "C", "mixed"], lambda row: row.right, summary.by_right.get),
        build_nodes(["gain", "loss"], lambda row: row.outcome, summary.by_outcome.get),
    ]
    if not rows:
        return {"width": width, "height": height, "empty": True}

    total = sum(abs(float(row.realized_pnl)) for row in rows) or 1.0
    for column in columns:
        n = len(column)
        usable = height - 2 * pad_y - gap * max(n - 1, 0)
        y = pad_y
        for node in column.values():
            node["h"] = max(usable * node["weight"] / total, 6.0)
            node["y"] = y
            node["cursor"] = y  # ribbon attachment offset
            y += node["h"] + gap

    def ribbon(source: dict, target: dict, weight: float, x1: float, x2: float) -> str:
        h1 = source["h"] * weight / source["weight"]
        h2 = target["h"] * weight / target["weight"]
        y1, y2 = source["cursor"], target["cursor"]
        source["cursor"] += h1
        target["cursor"] += h2
        mid = (x1 + x2) / 2
        return (
            f"M {x1:.1f} {y1:.1f} C {mid:.1f} {y1:.1f} {mid:.1f} {y2:.1f} {x2:.1f} {y2:.1f} "
            f"L {x2:.1f} {y2 + h2:.1f} C {mid:.1f} {y2 + h2:.1f} {mid:.1f} {y1 + h1:.1f} "
            f"{x1:.1f} {y1 + h1:.1f} Z"
        )

    ribbons = []
    for row in rows:
        weight = abs(float(row.realized_pnl))
        symbol_node = columns[0][row.underlying.code]
        right_node = columns[1][row.right]
        outcome_node = columns[2][row.outcome]
        css = "flow-gain" if row.outcome == "gain" else "flow-loss"
        ribbons.append(
            {
                "d": ribbon(symbol_node, right_node, weight, col_x[0] + node_w, col_x[1]),
                "css": "flow-mid",
            }
        )
        ribbons.append(
            {
                "d": ribbon(right_node, outcome_node, weight, col_x[1] + node_w, col_x[2]),
                "css": css,
            }
        )

    def node_list(column: dict, x: float, labels: dict | None, css: str, anchor: str) -> list:
        out = []
        for node in column.values():
            label = labels.get(node["key"], node["key"]) if labels else node["key"]
            out.append(
                {
                    "x": x,
                    "y": node["y"],
                    "h": node["h"],
                    "w": node_w,
                    "label": label,
                    "amount": node["amount"],  # library Decimal, formatted downstream
                    "share": node["share"],  # library fraction, formatted downstream
                    "label_x": x - 6 if anchor == "end" else x + node_w + 6,
                    "label_y": node["y"] + node["h"] / 2 + 4,
                    "anchor": anchor,
                    "css": css,
                }
            )
        return out

    nodes = (
        node_list(columns[0], col_x[0], None, "flow-node-symbol", "end")
        + node_list(columns[1], col_x[1], right_labels, "flow-node-right", "start")
        + node_list(columns[2], col_x[2], outcome_labels, "flow-node-outcome", "start")
    )
    return {"width": width, "height": height, "empty": False, "nodes": nodes, "ribbons": ribbons}


@register.inclusion_tag("optiontracker/charts/hbar_list.html")
def strategy_count_bars(strategy_counts: dict) -> dict:
    """Horizontal bar list widths for Strategies Count (counts, not money)."""
    items = sorted(strategy_counts.items(), key=lambda kv: kv[1], reverse=True)
    peak = max((count for _slug, count in items), default=1)
    return {
        "items": [
            {"slug": slug, "count": count, "width": count / peak * 100.0} for slug, count in items
        ]
    }
