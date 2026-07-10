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
import math

from django import template

from dev_project.optiontracker.templatetags.tracker_format import strategy_label

register = template.Library()

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

#: Fixed-order 10-color categorical palette for the strategies donut
#: (validated with the dataviz palette checker, dark + light surfaces).
_DONUT_PALETTE = [
    "#cc8104",
    "#5b6cff",
    "#ec4899",
    "#1da84f",
    "#a855f7",
    "#ef4444",
    "#08a5bf",
    "#b78a06",
    "#8b5cf6",
    "#12a695",
]


def _tick_label(value: float) -> str:
    """Reference axis captions: two-decimal thousands ("$180.00K",
    "-$10.00K") with zero rendered plainly as "$0.00"."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude < 0.005:
        return "$0.00"
    if magnitude >= 10:
        return f"{sign}${magnitude / 1000:,.2f}K"
    return f"{sign}${magnitude:,.2f}"


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if high <= low:
        high = low + 1.0
    span = (high - low) / max(count - 1, 1)
    ticks = [low + span * i for i in range(count)]
    return ticks


@register.inclusion_tag("optiontracker/charts/bar_chart.html")
def profit_bar_chart(profit: dict, mode: str = "monthly", goal: object = None) -> dict:
    """Vertical bars from PerformanceStats.monthly_profit / weekly_profit.

    `goal` is the raw user input from the "Weekly goal" field; drawing it
    as a dashed horizontal line is pixel geometry (presentation)."""
    width, height = 860.0, 300.0
    pad_left, pad_right, pad_top, pad_bottom = 64.0, 16.0, 16.0, 28.0
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    try:
        goal_value = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        goal_value = None
    if goal_value is not None and goal_value <= 0:
        goal_value = None

    items = sorted(profit.items())
    values = [float(amount) for _period, amount in items]
    low = min(0.0, *values) if values else 0.0
    high = max(0.0, *values) if values else 1.0
    if goal_value is not None:
        high = max(high, goal_value)
        low = min(low, goal_value)
    if high == low:
        high = low + 1.0

    def y_of(value: float) -> float:
        return pad_top + (high - value) / (high - low) * plot_h

    # Reference x-axis: month names in every mode. Weekly buckets caption
    # only the first bucket of each month; monthly buckets are thinned to
    # at most ~13 captions.
    label_step = max(1, math.ceil(len(items) / 13)) if mode != "weekly" else 1
    bars = []
    slot = plot_w / max(len(items), 1)
    bar_w = min(46.0, slot * 0.6)
    previous_month: tuple | None = None
    for index, (period, amount) in enumerate(items):
        value = float(amount)
        top = y_of(max(value, 0.0))
        bottom = y_of(min(value, 0.0))
        label = _MONTHS[period.month - 1]
        if mode == "weekly":
            if (period.year, period.month) == previous_month:
                label = ""
            previous_month = (period.year, period.month)
        bars.append(
            {
                "x": pad_left + slot * index + (slot - bar_w) / 2,
                "y": top,
                "w": bar_w,
                "h": max(bottom - top, 1.0),
                "negative": value < 0,
                "label": label if index % label_step == 0 else "",
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
        "goal_y": y_of(goal_value) if goal_value is not None else None,
        "goal_label": _tick_label(goal_value) if goal_value is not None else None,
        "pad_left": pad_left,
        "tick_x": pad_left - 8.0,
        "plot_right": width - pad_right,
        "label_y": height - 8.0,
    }


@register.inclusion_tag("optiontracker/charts/cumulative_chart.html")
def cumulative_profit_chart(
    daily_cumulative: list, mode: str = "weekly", goal: object = None
) -> dict:
    """Cumulative-profit line with area fill (the Cumulative Profits
    card's default look): PerformanceStats.daily_cumulative IS the
    running-profit curve; the x-axis captions month names in every mode
    (reference behavior). The goal input draws a dashed line."""
    width, height = 860.0, 300.0
    pad_left, pad_right, pad_top, pad_bottom = 64.0, 16.0, 16.0, 28.0
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    try:
        goal_value = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        goal_value = None
    if goal_value is not None and goal_value <= 0:
        goal_value = None

    if not daily_cumulative:
        return {"width": width, "height": height, "empty": True}

    days = [day.toordinal() for day, _value in daily_cumulative]
    x_low, x_high = min(days), max(days)
    if x_high == x_low:
        x_high = x_low + 1
    values = [float(value) for _day, value in daily_cumulative]
    low = min(0.0, *values)
    high = max(0.0, *values)
    if goal_value is not None:
        high = max(high, goal_value)
        low = min(low, goal_value)
    if high == low:
        high = low + 1.0

    def x_of(ordinal: int) -> float:
        return pad_left + (ordinal - x_low) / (x_high - x_low) * plot_w

    def y_of(value: float) -> float:
        return pad_top + (high - value) / (high - low) * plot_h

    points = [(x_of(day.toordinal()), y_of(float(value))) for day, value in daily_cumulative]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    baseline = y_of(max(0.0, low))
    area = f"{points[0][0]:.1f},{baseline:.1f} " + line + f" {points[-1][0]:.1f},{baseline:.1f}"

    ticks = [{"y": y_of(value), "label": _tick_label(value)} for value in _nice_ticks(low, high)]

    # Bucket labels (reference): month names in every mode — one caption
    # at the first data point of each month, thinned past ~13 captions.
    seen: set = set()
    x_ticks = []
    for day, _value in daily_cumulative:
        key = (day.year, day.month)
        if key in seen:
            continue
        seen.add(key)
        x_ticks.append({"x": x_of(day.toordinal()), "label": _MONTHS[day.month - 1]})
    step = max(1, math.ceil(len(x_ticks) / 13))
    x_ticks = x_ticks[::step]

    return {
        "width": width,
        "height": height,
        "empty": False,
        "line": line,
        "area": area,
        "ticks": ticks,
        "x_ticks": x_ticks,
        "zero_y": y_of(0.0),
        "goal_y": y_of(goal_value) if goal_value is not None else None,
        "goal_label": _tick_label(goal_value) if goal_value is not None else None,
        "pad_left": pad_left,
        "pad_top": pad_top,
        "plot_bottom": height - pad_bottom,
        "tick_x": pad_left - 8.0,
        "plot_right": width - pad_right,
        "label_y": height - 8.0,
    }


@register.inclusion_tag("optiontracker/charts/line_chart.html")
def cumulative_line_chart(daily_cumulative: list, account_values: list | None = None) -> dict:
    """Two report series on one time axis with DUAL y-axes (reference
    behavior): the LEFT axis is scaled to account_value_series() and the
    RIGHT axis to PerformanceStats.daily_cumulative, so both lines span
    the plot. Tick labels are derived scale marks on both sides."""
    width, height = 860.0, 300.0
    pad_left, pad_right, pad_top, pad_bottom = 64.0, 64.0, 16.0, 28.0
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    account_values = account_values or []
    if not daily_cumulative and not account_values:
        return {"width": width, "height": height, "empty": True}

    all_series = [series for series in (daily_cumulative, account_values) if series]
    all_days = [day.toordinal() for series in all_series for day, _v in series]
    x_low, x_high = min(all_days), max(all_days)
    if x_high == x_low:
        x_high = x_low + 1

    def scale_bounds(series: list, *, include_zero: bool) -> tuple[float, float]:
        values = [float(value) for _day, value in series]
        low = min(values) if values else 0.0
        high = max(values) if values else 1.0
        if include_zero:
            low, high = min(0.0, low), max(0.0, high)
        if high == low:
            high = low + 1.0
        return low, high

    profit_low, profit_high = scale_bounds(daily_cumulative or account_values, include_zero=True)
    account_low, account_high = scale_bounds(account_values or daily_cumulative, include_zero=False)

    def x_of(ordinal: int) -> float:
        return pad_left + (ordinal - x_low) / (x_high - x_low) * plot_w

    def y_scaled(value: float, low: float, high: float) -> float:
        return pad_top + (high - value) / (high - low) * plot_h

    def y_profit(value: float) -> float:
        return y_scaled(value, profit_low, profit_high)

    def y_account(value: float) -> float:
        return y_scaled(value, account_low, account_high)

    line = area = None
    if daily_cumulative:
        points = [
            (x_of(day.toordinal()), y_profit(float(value))) for day, value in daily_cumulative
        ]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        baseline = y_profit(max(0.0, profit_low))
        area = f"{points[0][0]:.1f},{baseline:.1f} " + line + f" {points[-1][0]:.1f},{baseline:.1f}"
    value_line = (
        " ".join(
            f"{x_of(day.toordinal()):.1f},{y_account(float(value)):.1f}"
            for day, value in account_values
        )
        if account_values
        else None
    )

    # Shared gridline rows; each row captions its own scale on each side.
    tick_count = 5
    ticks = []
    for i in range(tick_count):
        fraction = i / (tick_count - 1)
        y = pad_top + (1.0 - fraction) * plot_h
        left_value = account_low + (account_high - account_low) * fraction
        right_value = profit_low + (profit_high - profit_low) * fraction
        ticks.append(
            {
                "y": y,
                "left_label": _tick_label(left_value) if account_values else "",
                "right_label": _tick_label(right_value) if daily_cumulative else "",
            }
        )
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
        "tick_x_right": width - pad_right + 8.0,
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
                    # first of two label lines, clamped so the second line
                    # (dy 15) stays inside the viewBox
                    "label_y": min(max(node["y"] + node["h"] / 2 - 3, 14.0), height - 20.0),
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


#: Reference legend order for the Strategies Count donut (fixed vocabulary
#: first, anything else appended alphabetically).
_LEGEND_ORDER = [
    "Call Credit Spread",
    "Call Debit Spread",
    "Iron Condor",
    "Long Call",
    "Long Put",
    "Put Credit Spread",
    "Put Debit Spread",
    "Covered Call",
    "Cash Secured Put",
]


@register.inclusion_tag("optiontracker/charts/donut_chart.html")
def strategy_donut(strategy_counts: dict) -> dict:
    """Donut arcs for Strategies Count (counts, not money): fixed-order
    palette assigned by label order, arc geometry in pixels."""
    size = 280.0
    cx = cy = size / 2
    radius = 102.0
    stroke = 34.0
    gap_deg = 2.2

    # Legend/arc order: the reference's fixed strategy order.
    def legend_rank(slug: str) -> "tuple[int, str]":
        label = strategy_label(slug)
        rank = _LEGEND_ORDER.index(label) if label in _LEGEND_ORDER else len(_LEGEND_ORDER)
        return (rank, label.lower())

    items = sorted(strategy_counts.items(), key=lambda kv: legend_rank(kv[0]))
    total = 0
    for _slug, count in items:
        total += count
    if total <= 0:
        return {"size": size, "empty": True, "segments": [], "legend": []}

    def point(angle_deg: float) -> tuple[float, float]:
        rad = math.radians(angle_deg)
        return cx + radius * math.cos(rad), cy + radius * math.sin(rad)

    segments = []
    legend = []
    angle = -90.0
    gap = gap_deg if len(items) > 1 else 0.0
    for index, (slug, count) in enumerate(items):
        color = _DONUT_PALETTE[index % len(_DONUT_PALETTE)]
        share = count / total
        sweep = share * 360.0 - gap
        entry = {
            "label": strategy_label(slug),
            "count": count,
            "color": color,
            "share_label": f"{share * 100.0:.1f}%",
        }
        legend.append(entry)
        if len(items) == 1:
            segments.append({**entry, "full_circle": True, "d": ""})
            break
        sweep = max(sweep, 0.6)
        x1, y1 = point(angle + gap / 2)
        x2, y2 = point(angle + gap / 2 + sweep)
        large = 1 if sweep > 180.0 else 0
        segments.append(
            {
                **entry,
                "full_circle": False,
                "d": f"M {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 {x2:.2f} {y2:.2f}",
            }
        )
        angle += share * 360.0
    return {
        "size": size,
        "cx": cx,
        "cy": cy,
        "radius": radius,
        "stroke": stroke,
        "empty": False,
        "segments": segments,
        "legend": legend,
        "total": total,
    }
