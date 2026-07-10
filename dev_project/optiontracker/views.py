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

from django.core.paginator import Paginator
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


def _wheel_entries(
    campaigns: "list[reports.WheelCampaign]", legs_by_trade: "dict[int, list] | None" = None
) -> "list[dict]":
    legs_by_trade = legs_by_trade or {}
    return [
        {
            "campaign": campaign,
            "quote": services.price_source().get_quote(campaign.underlying),
            "legs": legs_by_trade.get(campaign.trade.pk, []),
        }
        for campaign in campaigns
    ]


def wheel(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "wheel")
    campaigns = reports.wheel_campaigns(user, services.price_source())
    context["total_pnl"] = reports.wheel_total_pnl(campaigns)

    query = request.GET.get("q", "").strip()
    if query:
        campaigns = [c for c in campaigns if query.upper() in c.underlying.code.upper()]

    # Each campaign's open covered-call legs, straight from the library
    # (selection by trade pk — no computation).
    legs_by_trade = {
        strategy.trade.pk: strategy.legs
        for strategy in reports.open_option_strategies(user, services.price_source())
    }
    context["entries"] = _wheel_entries(campaigns, legs_by_trade)
    context["query"] = query
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_wheel_table.html", context)
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
    query = request.GET.get("q", "").strip()
    mode = "weekly" if request.GET.get("mode") == "weekly" else "monthly"
    goal = request.GET.get("goal", "").strip()
    stats = reports.strategy_performance(
        user,
        strategies=selected or None,
        underlyings=[query] if query else None,
        start=_range_start(range_code),
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
    mode_state = {}
    for name in ("monthly", "weekly"):
        params = request.GET.copy()
        params["mode"] = name
        mode_state[name] = "?" + params.urlencode()
    context.update(
        {
            "stats": stats,
            "account_values": account_values,
            "strategy_options": strategy_options,
            "selected_strategies": selected,
            "range_code": range_code,
            "date_ranges": DATE_RANGES,
            "query": query,
            "mode": mode,
            "goal": goal,
            "mode_state": mode_state,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_analytics_body.html", context)
    return render(request, "optiontracker/analytics.html", context)


def pnl_flow_view(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "pnl_flow")
    query = request.GET.get("q", "").strip()
    selected = [s for s in request.GET.getlist("strategy") if s]
    range_code = request.GET.get("range", "all")
    start = _range_start(range_code)

    flow = reports.pnl_flow_summary(
        user,
        strategies=selected or None,
        underlyings=[query] if query else None,
        start=start,
    )
    top10_applied = False
    if not query:
        # Default view: the top 10 symbols by |realized amount| (selection/
        # slicing is presentation; totals stay library-computed via re-query).
        ranked = sorted(flow.by_symbol.items(), key=lambda kv: abs(kv[1]), reverse=True)
        if len(ranked) > 10:
            top_codes = [instrument.code for instrument, _amount in ranked[:10]]
            flow = reports.pnl_flow_summary(
                user, strategies=selected or None, underlyings=top_codes, start=start
            )
            top10_applied = True

    context.update(
        {
            "flow": flow,
            "top10_applied": top10_applied,
            "query": query,
            "selected_strategies": selected,
            "strategy_options": sorted(reports.strategy_performance(user).strategy_counts),
            "range_code": range_code,
            "date_ranges": DATE_RANGES,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_flow_body.html", context)
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

    query = request.GET.get("q", "").strip()
    view_mode = "day" if request.GET.get("view") == "day" else "month"
    days = reports.premium_calendar(user, year, month, underlyings=[query] if query else None)
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
    day_list = [{"date": day, "data": days[day]} for day in sorted(days)]
    prev_month = first - datetime.timedelta(days=1)
    next_month = (first + datetime.timedelta(days=32)).replace(day=1)
    nav_urls = {}
    for name, target in (("prev", prev_month), ("next", next_month)):
        params = request.GET.copy()
        params["year"] = target.year
        params["month"] = target.month
        nav_urls[name] = "?" + params.urlencode()
    context.update(
        {
            "weeks": weeks,
            "day_list": day_list,
            "view_mode": view_mode,
            "query": query,
            "month_date": first,
            "prev_url": nav_urls["prev"],
            "next_url": nav_urls["next"],
            "weekday_names": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_calendar_body.html", context)
    return render(request, "optiontracker/calendar.html", context)


def history(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "history")
    rows = reports.closed_option_strategies(user)
    strategy_options = sorted({row.strategy for row in rows if row.strategy})

    query = request.GET.get("q", "").strip()
    selected = [s for s in request.GET.getlist("strategy") if s]
    range_code = request.GET.get("range", "all")
    assigned_only = bool(request.GET.get("assigned"))
    start = _range_start(range_code)
    if query:
        rows = [row for row in rows if query.upper() in _symbol_of(row).upper()]
    if selected:
        rows = [row for row in rows if row.strategy in selected]
    if start:
        rows = [row for row in rows if row.closed_on and row.closed_on >= start]
    if assigned_only:
        rows = [row for row in rows if row.assigned]

    sort = request.GET.get("sort", "-trade_date")
    descending = sort.startswith("-")
    column = sort.lstrip("-")
    if column == "pnl":
        rows = sorted(rows, key=_none_last("net_profit"), reverse=descending)
    else:
        column = "trade_date"
        rows = sorted(rows, key=lambda row: row.closed_on or datetime.date.min, reverse=descending)

    sort_state = {}
    for name in ("trade_date", "pnl"):
        params = request.GET.copy()
        params.pop("page", None)
        is_active = column == name
        params["sort"] = f"-{name}" if is_active and not descending else name
        sort_state[name] = {
            "url": "?" + params.urlencode(),
            "active": is_active,
            "descending": is_active and descending,
        }

    paginator = Paginator(rows, 10)  # pagination is presentation
    page_obj = paginator.get_page(request.GET.get("page"))
    page_urls = {}
    if page_obj.has_previous():
        params = request.GET.copy()
        params["page"] = page_obj.previous_page_number()
        page_urls["prev"] = "?" + params.urlencode()
    if page_obj.has_next():
        params = request.GET.copy()
        params["page"] = page_obj.next_page_number()
        page_urls["next"] = "?" + params.urlencode()

    stats = reports.strategy_performance(
        user,
        strategies=selected or None,
        underlyings=[query] if query else None,
        start=start,
    )
    context.update(
        {
            "rows": page_obj.object_list,
            "page_obj": page_obj,
            "page_urls": page_urls,
            "stats": stats,
            "total_strategies": stats.wins + stats.losses,  # closure count, not money
            "strategy_options": strategy_options,
            "selected_strategies": selected,
            "range_code": range_code,
            "date_ranges": DATE_RANGES,
            "query": query,
            "assigned_only": assigned_only,
            "sort_state": sort_state,
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_history_table.html", context)
    return render(request, "optiontracker/history.html", context)


def broker(request: HttpRequest) -> HttpResponse:
    _user, context = _base_context(request, "broker")
    context["brokers"] = BROKERS
    return render(request, "optiontracker/broker.html", context)
