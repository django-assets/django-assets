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

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from dev_project.optiontracker import services
from django_assets.trades import reports
from django_assets.trades.models import Trade

#: Date Range menu vocabulary (reference dropdown). "" clears the filter;
#: "custom" reveals two date inputs + Apply in the menu.
DATE_RANGES = [
    ("", "Select date range"),
    ("this_week", "This Week"),
    ("last_week", "Last Week"),
    ("this_month", "This Month"),
    ("last_month", "Last Month"),
    ("this_quarter", "This Quarter"),
    ("ytd", "YTD"),
    ("custom", "Custom Date Range"),
]
RANGE_LABELS = dict(DATE_RANGES)

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

#: positions table sort keys -> OpenStrategy attributes (metric-toggle
#: aware variants are selected per request in option_positions).
POSITION_SORTS = {
    "expiration": "expiration",
    "pnl": "pnl_pct",
    "market_value": "market_value",
    "delta": "delta_pct",
    "moneyness": "moneyness_pct",
}

#: Roll Selection finder lookback windows (reference select: 60 days ..
#: 2 years; default 60).
ROLL_LOOKBACKS = (
    (60, "60 days"),
    (90, "90 days"),
    (180, "180 days"),
    (365, "1 year"),
    (730, "2 years"),
)
ROLL_LOOKBACK_DAYS = {days for days, _label in ROLL_LOOKBACKS}


def _parse_date(raw: str | None) -> datetime.date | None:
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _date_window(request: HttpRequest) -> "tuple[str, datetime.date | None, datetime.date | None]":
    """Date Range menu choice -> (code, start, end). Pure calendar-window
    arithmetic (presentation); the dates go to the library's start/end
    params. Weeks run Sunday..Saturday like the reference calendar."""
    code = request.GET.get("range", "")
    if code not in RANGE_LABELS:
        code = ""
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=(today.weekday() + 1) % 7)
    month_start = today.replace(day=1)
    if code == "this_week":
        return code, week_start, week_start + datetime.timedelta(days=6)
    if code == "last_week":
        return (
            code,
            week_start - datetime.timedelta(days=7),
            week_start - datetime.timedelta(days=1),
        )
    if code == "this_month":
        next_month = (month_start + datetime.timedelta(days=32)).replace(day=1)
        return code, month_start, next_month - datetime.timedelta(days=1)
    if code == "last_month":
        end = month_start - datetime.timedelta(days=1)
        return code, end.replace(day=1), end
    if code == "this_quarter":
        quarter_start = today.replace(month=today.month - (today.month - 1) % 3, day=1)
        next_quarter = (quarter_start + datetime.timedelta(days=95)).replace(day=1)
        return code, quarter_start, next_quarter - datetime.timedelta(days=1)
    if code == "ytd":
        return code, today.replace(month=1, day=1), today
    if code == "custom":
        return code, _parse_date(request.GET.get("start")), _parse_date(request.GET.get("end"))
    return "", None, None


def _range_context(request: HttpRequest, code: str) -> dict:
    """Template context for the Date Range dropdown (button label + the
    custom inputs' current values)."""
    label = RANGE_LABELS.get(code, "")
    return {
        "range_code": code,
        "range_label": label if code else "Date Range",
        "date_ranges": DATE_RANGES,
        "custom_start": request.GET.get("start", ""),
        "custom_end": request.GET.get("end", ""),
    }


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


def _leg_display_order(leg) -> "tuple[bool, bool]":
    """Reference leg order inside expanded rows: put pair before call
    pair, short before long within each right (pure presentation sort)."""
    return (leg.right != "P", leg.side != "short")


def _toggle_url(request: HttpRequest, param: str, value: str) -> str:
    params = request.GET.copy()
    params[param] = value
    return "?" + params.urlencode()


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

    # Header metric toggles (reference: blue header buttons switch the
    # displayed report attribute; a GET param keeps it bookmarkable).
    pnl_mode = "usd" if request.GET.get("pnl") == "usd" else "pct"
    metric = "extrinsic" if request.GET.get("metric") == "extrinsic" else "delta"
    sorts = dict(POSITION_SORTS)
    if pnl_mode == "usd":
        sorts["pnl"] = "pnl_incl_rolls"
    if metric == "extrinsic":
        sorts["delta"] = "extrinsic_value"

    sort = request.GET.get("sort", "symbol")
    descending = sort.startswith("-")
    column = sort.lstrip("-")
    if column in sorts:
        rows = sorted(rows, key=_none_last(sorts[column]), reverse=descending)
    else:
        column = "symbol"
        rows = sorted(rows, key=_symbol_of, reverse=descending)

    sort_state = {}
    for name in ("symbol", *sorts):
        params = request.GET.copy()
        is_active = column == name
        params["sort"] = f"-{name}" if is_active and not descending else name
        sort_state[name] = {
            "url": "?" + params.urlencode(),
            "active": is_active,
            "descending": is_active and descending,
        }

    entries = []
    for row in rows:
        entries.append(
            {
                "row": row,
                "underlying_price": _underlying_price(row),
                "legs": sorted(row.legs, key=_leg_display_order),
            }
        )
    context.update(
        {
            "entries": entries,
            "count": len(entries),
            "query": query,
            "selected_strategies": selected,
            "strategy_options": strategy_options,
            "sort_state": sort_state,
            "pnl_mode": pnl_mode,
            "metric": metric,
            "pnl_toggle_url": _toggle_url(request, "pnl", "pct" if pnl_mode == "usd" else "usd"),
            "metric_toggle_url": _toggle_url(
                request, "metric", "delta" if metric == "extrinsic" else "extrinsic"
            ),
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_positions_table.html", context)
    return render(request, "optiontracker/positions.html", context)


def roll_finder(request: HttpRequest, trade_pk: int) -> HttpResponse:
    """The Roll Selection dialog body: PRIOR closed trades on the same
    underlying that this open position could be linked to as a roll,
    straight from reports.roll_link_candidates(). Pure ledger data (no
    metered chain read), but still fetched on dialog open — never on page
    load — to keep the positions rows light."""
    user = services.demo_user()
    trade = get_object_or_404(Trade, pk=trade_pk, user=user)
    try:
        lookback = int(request.GET.get("lookback", "60"))
    except ValueError:
        lookback = 60
    if lookback not in ROLL_LOOKBACK_DAYS:
        lookback = 60
    link = reports.roll_link_candidates(
        user, trade, services.price_source(), lookback_days=lookback
    )
    # The dialog title's CALL/PUT + SHORT/LONG come from the open row's
    # primary leg (the same legs[0] whose strike RollLink reports).
    leg = None
    if link is not None:
        current = next(
            (
                row
                for row in reports.open_option_strategies(user, services.price_source())
                if row.trade == trade
            ),
            None,
        )
        if current is not None and current.legs:
            leg = current.legs[0]
    return render(
        request,
        "optiontracker/_roll_finder.html",
        {"link": link, "leg": leg, "lookbacks": ROLL_LOOKBACKS},
    )


def wheel(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "wheel")
    campaigns = reports.wheel_campaigns(user, services.price_source())
    context["total_pnl"] = reports.wheel_total_pnl(campaigns)

    query = request.GET.get("q", "").strip()
    if query:
        campaigns = [c for c in campaigns if query.upper() in c.underlying.code.upper()]

    pnl_mode = "usd" if request.GET.get("pnl") == "usd" else "pct"
    context["entries"] = [
        {"campaign": campaign, "quote": services.price_source().get_quote(campaign.underlying)}
        for campaign in campaigns
    ]
    context["query"] = query
    context["pnl_mode"] = pnl_mode
    context["pnl_toggle_url"] = _toggle_url(request, "pnl", "pct" if pnl_mode == "usd" else "usd")
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_wheel_table.html", context)
    return render(request, "optiontracker/wheel.html", context)


def equities(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "equities")
    holdings = reports.equity_holdings(
        user, services.price_source(), accounts=services.user_accounts(user)
    )

    query = request.GET.get("q", "").strip()
    if query:
        holdings = [h for h in holdings if query.upper() in h.instrument.code.upper()]

    pnl_mode = "usd" if request.GET.get("pnl") == "usd" else "pct"
    context["entries"] = [
        {"holding": holding, "quote": services.price_source().get_quote(holding.instrument)}
        for holding in holdings
    ]
    context["query"] = query
    context["pnl_mode"] = pnl_mode
    context["pnl_toggle_url"] = _toggle_url(request, "pnl", "pct" if pnl_mode == "usd" else "usd")
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_equities_table.html", context)
    return render(request, "optiontracker/equities.html", context)


def analytics(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "analytics")
    selected = [s for s in request.GET.getlist("strategy") if s]
    range_code, start, end = _date_window(request)
    query = request.GET.get("q", "").strip()
    mode = "monthly" if request.GET.get("mode") == "monthly" else "weekly"
    chart = "bars" if request.GET.get("chart") == "bars" else "cumulative"
    goal = request.GET.get("goal", "").strip()
    stats = reports.strategy_performance(
        user,
        strategies=selected or None,
        underlyings=[query] if query else None,
        start=start,
        end=end,
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
            **_range_context(request, range_code),
            "query": query,
            "mode": mode,
            "chart": chart,
            "chart_toggle_url": _toggle_url(
                request, "chart", "bars" if chart == "cumulative" else "cumulative"
            ),
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
    range_code, start, end = _date_window(request)

    flow = reports.pnl_flow_summary(
        user,
        strategies=selected or None,
        underlyings=[query] if query else None,
        start=start,
        end=end,
    )
    top10_applied = False
    if not query:
        # Default view: the top 10 symbols by |realized amount| (selection/
        # slicing is presentation; totals stay library-computed via re-query).
        ranked = sorted(flow.by_symbol.items(), key=lambda kv: abs(kv[1]), reverse=True)
        if len(ranked) > 10:
            top_codes = [instrument.code for instrument, _amount in ranked[:10]]
            flow = reports.pnl_flow_summary(
                user, strategies=selected or None, underlyings=top_codes, start=start, end=end
            )
            top10_applied = True

    context.update(
        {
            "flow": flow,
            "top10_applied": top10_applied,
            "query": query,
            "selected_strategies": selected,
            "strategy_options": sorted(reports.strategy_performance(user).strategy_counts),
            **_range_context(request, range_code),
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
    # Reference vocabulary: the "Day" view IS the day-grid calendar
    # (default, premium paychecks); "Week" lists the year's ISO weeks and
    # "Month" is the Jan–Dec aggregate-card grid (both realized PnL).
    view_mode = request.GET.get("view", "day")
    if view_mode not in ("day", "week", "month"):
        view_mode = "day"

    weeks = []
    week_cards = []
    month_cards = []
    if view_mode == "day":
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
    elif view_mode == "week":
        periods = reports.realized_weeks(user, year, underlyings=[query] if query else None)
        try:
            quarter = int(request.GET.get("quarter", (month - 1) // 3 + 1))
        except ValueError:
            quarter = (month - 1) // 3 + 1
        quarter = min(max(quarter, 1), 4)
        # Enumerate EVERY ISO week overlapping the quarter (empty weeks
        # render as $0.00, like the reference), Monday-keyed. Calendar
        # enumeration is presentation; PnL comes from realized_weeks.
        q_start = datetime.date(year, (quarter - 1) * 3 + 1, 1)
        q_end_month = quarter * 3
        q_end = (datetime.date(year, q_end_month, 1) + datetime.timedelta(days=31)).replace(
            day=1
        ) - datetime.timedelta(days=1)
        first_monday = q_start - datetime.timedelta(days=q_start.weekday())
        monday = first_monday
        while monday <= q_end:
            iso = monday.isocalendar()
            week_cards.append(
                {
                    "date": monday,
                    "iso_week": iso.week,
                    "iso_year": iso.year,
                    "data": periods.get(monday),  # None -> template renders $0.00
                }
            )
            monday += datetime.timedelta(days=7)
        context["quarter"] = quarter
        context["quarter_options"] = [1, 2, 3, 4]
    else:
        months = reports.realized_months(user, year, underlyings=[query] if query else None)
        month_cards = [
            {
                "date": datetime.date(year, number, 1),
                "data": months.get(number),
                "is_current": year == today.year and number == today.month,
            }
            for number in range(1, 13)
        ]

    prev_month = first - datetime.timedelta(days=1)
    next_month = (first + datetime.timedelta(days=32)).replace(day=1)
    nav_urls = {}
    if view_mode == "day":
        targets = (
            ("prev", prev_month.year, prev_month.month),
            ("next", next_month.year, next_month.month),
        )
    elif view_mode == "week":  # step whole quarters
        q = context["quarter"]
        prev_q = (year - 1, 4) if q == 1 else (year, q - 1)
        next_q = (year + 1, 1) if q == 4 else (year, q + 1)
        targets = (("prev", prev_q), ("next", next_q))
        nav_urls = {}
        for name, (ty, tq) in targets:
            params = request.GET.copy()
            params["year"] = ty
            params["quarter"] = tq
            nav_urls[name] = "?" + params.urlencode()
        this_q = (today.month - 1) // 3 + 1
        next_disabled = next_q > (today.year, this_q)
        targets = ()  # handled above
    else:  # Month view: the arrows step whole years
        targets = (("prev", year - 1, month), ("next", year + 1, month))
    for name, target_year, target_month in targets:
        params = request.GET.copy()
        params["year"] = target_year
        params["month"] = target_month
        nav_urls[name] = "?" + params.urlencode()
    # Reference greys the forward arrow once it would step past today.
    if view_mode == "day":
        next_disabled = (next_month.year, next_month.month) > (today.year, today.month)
    elif view_mode == "month":
        next_disabled = year + 1 > today.year
    context.update(
        {
            "weeks": weeks,
            "week_cards": week_cards,
            "month_cards": month_cards,
            "view_mode": view_mode,
            "query": query,
            "month_date": first,
            "month_options": [datetime.date(year, number, 1) for number in range(1, 13)],
            "year_options": [today.year - offset for offset in range(4)],
            "prev_url": nav_urls["prev"],
            "next_url": nav_urls["next"],
            "next_disabled": next_disabled,
            "weekday_names": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        }
    )
    if request.headers.get("HX-Request"):
        return render(request, "optiontracker/_calendar_body.html", context)
    return render(request, "optiontracker/calendar.html", context)


def calendar_month_detail(request: HttpRequest, year: int, month: int) -> HttpResponse:
    """The calendar's month-detail dialog body: one month's realized-PnL
    breakdown, every figure finished by reports.month_detail()."""
    try:
        detail_date = datetime.date(year, month, 1)
    except ValueError as error:
        raise Http404("no such month") from error
    detail = reports.month_detail(services.demo_user(), year, month)
    # Adjacent months for the dialog's ‹ › chevrons (date arithmetic only;
    # the chevrons re-load this same endpoint for the neighboring month).
    prev_month = detail_date - datetime.timedelta(days=1)
    next_month = (detail_date + datetime.timedelta(days=32)).replace(day=1)
    return render(
        request,
        "optiontracker/_month_detail.html",
        {
            "detail": detail,
            "detail_date": detail_date,
            "prev_month": prev_month,
            "next_month": next_month,
        },
    )


def history(request: HttpRequest) -> HttpResponse:
    user, context = _base_context(request, "history")
    query = request.GET.get("q", "").strip()
    selected = [s for s in request.GET.getlist("strategy") if s]
    assigned_only = bool(request.GET.get("assigned"))
    range_code, start, end = _date_window(request)

    all_rows = reports.closed_option_strategies(user)
    strategy_options = sorted({row.strategy for row in all_rows if row.strategy})

    if assigned_only:
        # Reference: the Assigned checkbox swaps in a different table —
        # one row per assignment event, from reports.assignments().
        assignment_rows = reports.assignments(user)
        if query:
            assignment_rows = [
                row for row in assignment_rows if query.upper() in row.underlying.code.upper()
            ]
        if start:
            assignment_rows = [row for row in assignment_rows if row.assigned_on >= start]
        if end:
            assignment_rows = [row for row in assignment_rows if row.assigned_on <= end]
        context["assignments"] = assignment_rows
        context["total_assignments"] = len(assignment_rows)
        rows = []
        sort_state = {}
    else:
        rows = all_rows
        if query:
            rows = [row for row in rows if query.upper() in _symbol_of(row).upper()]
        if selected:
            rows = [row for row in rows if row.strategy in selected]
        if start:
            rows = [row for row in rows if row.closed_on and row.closed_on >= start]
        if end:
            rows = [row for row in rows if row.closed_on and row.closed_on <= end]

        sort = request.GET.get("sort", "-trade_date")
        descending = sort.startswith("-")
        column = sort.lstrip("-")
        if column == "pnl":
            rows = sorted(rows, key=_none_last("net_profit"), reverse=descending)
        else:
            column = "trade_date"
            rows = sorted(
                rows, key=lambda row: row.closed_on or datetime.date.min, reverse=descending
            )

        sort_state = {}
        for name in ("trade_date", "pnl"):
            params = request.GET.copy()
            is_active = column == name
            params["sort"] = f"-{name}" if is_active and not descending else name
            sort_state[name] = {
                "url": "?" + params.urlencode(),
                "active": is_active,
                "descending": is_active and descending,
            }

    # Reference behavior: the totals above the filter row are GRAND totals
    # (whole account), regardless of the active search/strategy/date filters.
    stats = reports.strategy_performance(user)
    context.update(
        {
            "rows": rows,  # all rows on one page (reference has no pagination)
            "stats": stats,
            "total_strategies": stats.wins + stats.losses,  # closure count, not money
            "strategy_options": strategy_options,
            "selected_strategies": selected,
            **_range_context(request, range_code),
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
