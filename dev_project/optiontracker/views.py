"""Thin views: fetch library reports, hand them to templates.

No domain logic — every money/greek/ratio figure comes finished from
django_assets.trades.reports. The only work done here is presentation
plumbing: request-param parsing, list filtering/sorting by report
attributes, date-window selection, and calendar grid layout (dates,
never money).
"""

import calendar as calendar_mod
import datetime
from operator import attrgetter

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from dev_project.optiontracker import services
from django_assets.trades import reports

DATE_RANGES = [
    ("all", "All time"),
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
    ("ytd", "Year to date"),
    ("1y", "Last year"),
]

BROKERS = [
    ("Robinhood", "RH", False),
    ("Charles Schwab", "CS", False),
    ("Fidelity", "F", False),
    ("E*Trade", "ET", False),
    ("Webull", "W", False),
    ("Tastytrade", "TT", False),
    ("Interactive Brokers", "IB", True),
    ("Moomoo", "M", True),
]

#: positions table sort keys -> OpenStrategy attributes
POSITION_SORTS = {
    "expiration": "expiration",
    "pnl": "pnl_pct",
    "market_value": "market_value",
    "delta": "delta_pct",
    "moneyness": "moneyness_pct",
}


def _range_start(code: str) -> datetime.date | None:
    today = datetime.date.today()
    if code == "30d":
        return today - datetime.timedelta(days=30)
    if code == "90d":
        return today - datetime.timedelta(days=90)
    if code == "ytd":
        return today.replace(month=1, day=1)
    if code == "1y":
        return today - datetime.timedelta(days=365)
    return None


def _base_context(request: HttpRequest, nav: str) -> "tuple[object, dict]":
    user = services.demo_user()
    summary = reports.account_summary(
        user, services.price_source(), accounts=services.user_accounts(user)
    )
    is_htmx = bool(request.headers.get("HX-Request"))
    return user, {"summary": summary, "nav": nav, "is_htmx": is_htmx}


def _underlying_price(row: reports.OpenStrategy):
    """First leg quote carrying the vendor's underlying price (selection,
    not computation)."""
    for leg in row.legs:
        underlying_price = getattr(leg.quote, "underlying_price", None)
        if underlying_price is not None:
            return underlying_price
    return None


def _symbol_of(row) -> str:
    return row.underlying.code if row.underlying is not None else ""


def _none_last(attr: str):
    getter = attrgetter(attr)

    def key(row):
        value = getter(row)
        return (value is None, value)

    return key


def option_positions(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "positions")
    rows = reports.open_option_strategies(user, services.price_source())

    strategy_options = sorted({row.strategy for row in rows if row.strategy})

    query = request.GET.get("q", "").strip()
    selected = [s for s in request.GET.getlist("strategy") if s]
    if query:
        rows = [row for row in rows if query.upper() in _symbol_of(row).upper()]
    if selected:
        rows = [row for row in rows if row.strategy in selected]

    sort = request.GET.get("sort", "symbol")
    descending = sort.startswith("-")
    column = sort.lstrip("-")
    if column in POSITION_SORTS:
        rows = sorted(rows, key=_none_last(POSITION_SORTS[column]), reverse=descending)
    else:
        column = "symbol"
        rows = sorted(rows, key=_symbol_of, reverse=descending)

    sort_state = {}
    for name in ("symbol", *POSITION_SORTS):
        params = request.GET.copy()
        is_active = column == name
        params["sort"] = f"-{name}" if is_active and not descending else name
        sort_state[name] = {
            "url": "?" + params.urlencode(),
            "active": is_active,
            "descending": is_active and descending,
        }

    entries = [{"row": row, "underlying_price": _underlying_price(row)} for row in rows]
    context.update(
        {
            "entries": entries,
            "count": len(entries),
            "query": query,
            "selected_strategies": selected,
            "strategy_options": strategy_options,
            "sort_state": sort_state,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_positions_table.html", context)
    return render(request, "optiontracker/positions.html", context)


def _wheel_entries(campaigns: "list[reports.WheelCampaign]") -> "list[dict]":
    return [
        {"campaign": campaign, "quote": services.price_source().get_quote(campaign.underlying)}
        for campaign in campaigns
    ]


def wheel(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "wheel")
    campaigns = reports.wheel_campaigns(user, services.price_source())
    context["entries"] = _wheel_entries(campaigns)
    context["total_pnl"] = reports.wheel_total_pnl(campaigns)
    return render(request, "optiontracker/wheel.html", context)


def equities(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "equities")
    campaigns = reports.wheel_campaigns(user, services.price_source())
    context["entries"] = _wheel_entries(campaigns)
    return render(request, "optiontracker/equities.html", context)


def analytics(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "analytics")
    selected = [s for s in request.GET.getlist("strategy") if s]
    range_code = request.GET.get("range", "all")
    stats = reports.strategy_performance(
        user, strategies=selected or None, start=_range_start(range_code)
    )
    strategy_options = sorted(reports.strategy_performance(user).strategy_counts)
    today = datetime.date.today()
    account_values = reports.account_value_series(
        user,
        services.price_source(),
        accounts=services.user_accounts(user),
        start=today - datetime.timedelta(days=180),
        end=today,
    )
    context.update(
        {
            "stats": stats,
            "account_values": account_values,
            "strategy_options": strategy_options,
            "selected_strategies": selected,
            "range_code": range_code,
            "date_ranges": DATE_RANGES,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_analytics_body.html", context)
    return render(request, "optiontracker/analytics.html", context)


def pnl_flow_view(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "pnl_flow")
    context["flow"] = reports.pnl_flow_summary(user)
    return render(request, "optiontracker/pnl_flow.html", context)


def calendar_view(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "calendar")
    today = datetime.date.today()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        first = datetime.date(year, month, 1)
    except ValueError:
        year, month = today.year, today.month
        first = today.replace(day=1)

    days = reports.premium_calendar(user, year, month)
    grid = calendar_mod.Calendar(firstweekday=6)  # Sun..Sat like the reference
    weeks = [
        [
            {
                "date": day,
                "in_month": day.month == month,
                "is_today": day == today,
                "data": days.get(day),
            }
            for day in week
        ]
        for week in grid.monthdatescalendar(year, month)
    ]
    prev_month = first - datetime.timedelta(days=1)
    next_month = (first + datetime.timedelta(days=32)).replace(day=1)
    context.update(
        {
            "weeks": weeks,
            "month_date": first,
            "prev": {"year": prev_month.year, "month": prev_month.month},
            "next": {"year": next_month.year, "month": next_month.month},
            "weekday_names": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        }
    )
    return render(request, "optiontracker/calendar.html", context)


def history(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "history")
    rows = reports.closed_option_strategies(user)
    strategy_options = sorted({row.strategy for row in rows if row.strategy})

    selected = [s for s in request.GET.getlist("strategy") if s]
    range_code = request.GET.get("range", "all")
    start = _range_start(range_code)
    if selected:
        rows = [row for row in rows if row.strategy in selected]
    if start:
        rows = [row for row in rows if row.closed_on and row.closed_on >= start]
    rows = sorted(rows, key=lambda row: row.closed_on or datetime.date.min, reverse=True)

    stats = reports.strategy_performance(user, strategies=selected or None, start=start)
    context.update(
        {
            "rows": rows,
            "stats": stats,
            "total_strategies": stats.wins + stats.losses,  # closure count, not money
            "strategy_options": strategy_options,
            "selected_strategies": selected,
            "range_code": range_code,
            "date_ranges": DATE_RANGES,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_history_table.html", context)
    return render(request, "optiontracker/history.html", context)


def broker(request: HttpRequest) -> HttpResponse:
    _user, context = _base_context(request, "broker")
    context["brokers"] = BROKERS
    return render(request, "optiontracker/broker.html", context)
