"""Trades reporting surface (option-tracker vertical): every number a
dashboard renders comes from here — the app layer does presentation only.

Scenario fixtures mirror the reference option tracker: a put credit
spread with live greeks, a rolled short put with per-segment history, a
closed strategy with fees, a wheel campaign with premium-adjusted cost.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Instrument
from django_assets.core.prices import OptionQuote, PriceKind, PriceQuote
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.models import Trade
from django_assets.trades.reports import (
    account_summary,
    closed_option_strategies,
    open_option_strategies,
    pnl_flow,
    premium_calendar,
    roll_segments,
    strategy_performance,
    wheel_campaigns,
)

pytestmark = pytest.mark.django_db

D = Decimal
UTC = datetime.UTC


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2, price_decimals=2)


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "market"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


@pytest.fixture
def spy(usd):
    return Instrument.objects.create(
        code="SPY", quantity_decimals=4, price_decimals=2, price_currency=usd
    )


def make_option(usd, underlying, *, strike, right, expiry=datetime.date(2026, 8, 21)):
    instrument = Instrument.objects.create(
        code=f"{underlying.code}{expiry:%y%m%d}{right}{strike}",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    OptionMeta.objects.create(
        instrument=instrument,
        underlying=underlying,
        expiry=expiry,
        strike=D(strike),
        right=right,
    )
    return instrument


def book(accounts, usd, *, ts, position_legs, cash=None, fee=None, description=""):
    """One template-shaped transaction: mirrored position pairs +
    settlement cash + optional fee leg."""
    with TransactionBuilder(account=accounts["cash"], timestamp=ts, description=description) as b:
        for instrument, amount in position_legs:
            b.add_leg(account=accounts["holdings"], instrument=instrument, amount=str(amount))
            b.add_leg(account=accounts["market"], instrument=instrument, amount=str(-amount))
        if cash is not None:
            b.add_leg(account=accounts["cash"], instrument=usd, amount=str(cash))
            b.add_leg(account=accounts["market"], instrument=usd, amount=str(-D(cash)))
        if fee is not None:
            b.add_leg(account=accounts["cash"], instrument=usd, amount=str(-D(fee)))
            b.add_leg(account=accounts["market"], instrument=usd, amount=str(fee))
    return b.transaction


class Marks:
    """A PriceSource-shaped stub with per-instrument quotes (options
    carry greeks); enough of the v2 surface for the reports."""

    def __init__(self, quotes):
        self.quotes = quotes

    def capabilities(self, instrument):
        return None

    def get_quote(self, instrument, *, kind=None):
        return self.quotes.get(instrument)

    def get_quotes(self, instruments, *, kind=None):
        return {inst: self.quotes.get(inst) for inst in instruments}

    def get_close(self, instrument, on):
        return None

    def get_ohlcv(self, instrument, *, start, end, resolution=None):
        return None


OPEN_TS = datetime.datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
EXPIRY = datetime.date(2026, 8, 21)


@pytest.fixture
def pcs(user, usd, accounts, spy):
    """Reference-shaped put credit spread: 5× short 190P / long 185P,
    net credit 450.25, fee 6.50."""
    short_put = make_option(usd, spy, strike="190", right="P")
    long_put = make_option(usd, spy, strike="185", right="P")
    tx = book(
        accounts,
        usd,
        ts=OPEN_TS,
        position_legs=[(short_put, -5), (long_put, 5)],
        cash="450.25",
        fee="6.50",
        description="open PCS",
    )
    trade = Trade.objects.create(user=user, name="SPY PCS")
    trade.assign(tx, fraction=1)
    trade.add_tag("strategy", "bull_put_spread")
    return {"trade": trade, "short": short_put, "long": long_put}


@pytest.fixture
def marks(pcs, spy, usd):
    return Marks(
        {
            spy: PriceQuote(
                price=D("182.45"), currency=usd, as_of=None, source="t", kind=PriceKind.DELAYED
            ),
            pcs["short"]: OptionQuote(
                price=D("2.15"),
                currency=usd,
                as_of=None,
                source="t",
                kind=PriceKind.DELAYED,
                iv=D("0.5234"),
                delta=D("-0.3422"),
                gamma=D("0.0457"),
                theta=D("-0.1235"),
                vega=D("0.0789"),
                underlying_price=D("182.45"),
            ),
            pcs["long"]: OptionQuote(
                price=D("1.42"),
                currency=usd,
                as_of=None,
                source="t",
                kind=PriceKind.DELAYED,
                iv=D("0.5678"),
                delta=D("-0.2346"),
                gamma=D("0.0346"),
                theta=D("-0.0988"),
                vega=D("0.0568"),
                underlying_price=D("182.45"),
            ),
        }
    )


# -- open strategies -----------------------------------------------------------


def test_open_strategy_row_matches_reference_shape(user, pcs, marks, spy, usd):
    rows = open_option_strategies(user, marks)
    assert len(rows) == 1
    row = rows[0]
    assert row.trade == pcs["trade"]
    assert row.strategy == "bull_put_spread"
    assert row.underlying == spy
    assert row.contracts == 5
    assert row.expiration == EXPIRY
    assert row.market_value == D("365.00")  # |(-5×2.15 + 5×1.42) × 100|
    assert row.initial_premium == D("450.25")  # fee excluded (its own category)
    assert row.unrealized_pnl == D("85.25")
    assert row.pnl_pct == D("85.25") / D("450.25")
    assert row.delta_pct == D("0.3422")  # primary leg = max |delta|
    assert row.moneyness == "ITM"  # short 190 put, underlying 182.45
    assert row.moneyness_pct == (D("190") - D("182.45")) / D("182.45")
    assert row.opened_on == OPEN_TS.date()
    assert row.margin_estimate == D("2500")  # 5-wide × 5 × 100 defined risk
    days = (EXPIRY - OPEN_TS.date()).days
    assert row.aroi_initial == D("450.25") / D("2500") * 365 / days


def test_open_strategy_legs_carry_quotes(user, pcs, marks):
    row = open_option_strategies(user, marks)[0]
    assert len(row.legs) == 2
    short = next(leg for leg in row.legs if leg.side == "short")
    long_ = next(leg for leg in row.legs if leg.side == "long")
    assert short.right == "P" and long_.right == "P"
    assert short.strike == D("190")
    assert short.contracts == D("5")
    assert isinstance(short.quote, OptionQuote)
    assert short.quote.iv == D("0.5234")
    assert long_.quote.delta == D("-0.2346")


def test_open_strategies_unpriced_surfaced(user, pcs):
    rows = open_option_strategies(user, Marks({}))
    row = rows[0]
    assert row.market_value is None
    assert row.pnl_pct is None
    assert row.delta_pct is None
    assert set(row.unpriced) == {pcs["short"], pcs["long"]}
    assert row.initial_premium == D("450.25")  # ledger facts still report


def test_closed_trades_not_in_open_report(user, usd, accounts, spy, marks):
    csp = make_option(usd, spy, strike="180", right="P")
    open_tx = book(accounts, usd, ts=OPEN_TS, position_legs=[(csp, -5)], cash="250.00")
    close_tx = book(
        accounts,
        usd,
        ts=OPEN_TS + datetime.timedelta(days=3),
        position_legs=[(csp, 5)],
        cash="-100.00",
    )
    trade = Trade.objects.create(user=user, name="closed csp")
    trade.assign(open_tx, fraction=1)
    trade.assign(close_tx, fraction=1)
    assert all(row.trade != trade for row in open_option_strategies(user, marks))


# -- rolls ------------------------------------------------------------------------


@pytest.fixture
def rolled(user, usd, accounts, spy):
    """Reference-shaped roll: short put A opened for 500.82, bought back
    for 100.15 (realized +400.67), rolled into short put B for 530.06."""
    put_a = make_option(usd, spy, strike="50", right="P", expiry=datetime.date(2026, 7, 17))
    put_b = make_option(usd, spy, strike="48", right="P", expiry=EXPIRY)
    t0 = datetime.datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    t1 = datetime.datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
    trade = Trade.objects.create(user=user, name="rolled put")
    trade.assign(book(accounts, usd, ts=t0, position_legs=[(put_a, -5)], cash="500.82"), fraction=1)
    trade.assign(book(accounts, usd, ts=t1, position_legs=[(put_a, 5)], cash="-100.15"), fraction=1)
    trade.assign(
        book(
            accounts,
            usd,
            ts=t1 + datetime.timedelta(minutes=1),
            position_legs=[(put_b, -5)],
            cash="530.06",
        ),
        fraction=1,
    )
    trade.add_tag("strategy", "short_put")
    return {"trade": trade, "a": put_a, "b": put_b, "t0": t0, "t1": t1}


def test_roll_segments_derive_history(rolled):
    segments = roll_segments(rolled["trade"])
    assert len(segments) == 1
    segment = segments[0]
    assert segment.opened_on == rolled["t0"].date()
    assert segment.closed_on == rolled["t1"].date()
    assert segment.initial_premium == D("500.82")
    assert segment.realized_pnl == D("400.67")


def test_rolled_open_report_premiums(user, rolled, usd, spy):
    marks = Marks(
        {
            spy: PriceQuote(
                price=D("57.20"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
            rolled["b"]: OptionQuote(
                price=D("1.94"),
                currency=usd,
                as_of=None,
                source="t",
                kind=PriceKind.EOD,
                delta=D("-0.2322"),
            ),
        }
    )
    row = next(r for r in open_option_strategies(user, marks) if r.trade == rolled["trade"])
    assert row.initial_premium == D("530.06")  # the live segment's own premium
    # premium incl. rolls = live premium + Σ realized of closed segments
    assert row.premium_incl_rolls == D("530.06") + D("400.67")
    assert row.opened_on == rolled["t1"].date()  # live segment's open
    assert len(row.rolls) == 1


def test_covered_call_pnl_is_option_side_only(user, usd, accounts, spy):
    """A covered call's PnL% measures the OPTION leg against its premium
    — the share leg's unrealized swing must not leak in (the reference
    dashboard's PnL% column is option-side)."""
    cc = make_option(usd, spy, strike="90", right="C")
    trade = Trade.objects.create(user=user, name="cc")
    trade.assign(
        book(accounts, usd, ts=OPEN_TS, position_legs=[(spy, 100)], cash="-8500.00"),
        fraction=1,
    )
    trade.assign(
        book(
            accounts,
            usd,
            ts=OPEN_TS + datetime.timedelta(days=1),
            position_legs=[(cc, -1)],
            cash="217.95",
        ),
        fraction=1,
    )
    trade.add_tag("strategy", "covered_call")
    marks = Marks(
        {
            # shares are 15% under water; the option decayed to 0.40
            spy: PriceQuote(
                price=D("72.25"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
            cc: OptionQuote(
                price=D("0.40"),
                currency=usd,
                as_of=None,
                source="t",
                kind=PriceKind.EOD,
                delta=D("0.1402"),
            ),
        }
    )
    row = open_option_strategies(user, marks)[0]
    assert row.initial_premium == D("217.95")
    assert row.unrealized_pnl == D("217.95") - D("40.00")  # premium − cost to close
    assert row.pnl_pct == (D("217.95") - D("40.00")) / D("217.95")
    assert row.market_value == D("40.00")  # option legs only


# -- closed strategies / history ----------------------------------------------------


@pytest.fixture
def closed(user, usd, accounts, spy):
    csp = make_option(usd, spy, strike="16", right="P", expiry=datetime.date(2026, 6, 19))
    t0 = datetime.datetime(2026, 5, 28, 14, 0, tzinfo=UTC)
    t1 = datetime.datetime(2026, 6, 6, 14, 0, tzinfo=UTC)
    trade = Trade.objects.create(user=user, name="ZZ csp")
    trade.assign(
        book(accounts, usd, ts=t0, position_legs=[(csp, -10)], cash="614.86", fee="6.50"),
        fraction=1,
    )
    trade.assign(
        book(accounts, usd, ts=t1, position_legs=[(csp, 10)], cash="-168.61", fee="6.50"),
        fraction=1,
    )
    trade.add_tag("strategy", "short_put")
    return {"trade": trade, "csp": csp, "t0": t0, "t1": t1}


def test_closed_strategies_report(user, closed, spy):
    rows = closed_option_strategies(user)
    assert len(rows) == 1
    row = rows[0]
    assert row.trade == closed["trade"]
    assert row.strategy == "short_put"
    assert row.underlying == spy
    assert row.contracts == 10
    assert row.expiration == datetime.date(2026, 6, 19)
    assert row.opened_on == closed["t0"].date()
    assert row.closed_on == closed["t1"].date()
    assert row.initial_premium == D("614.86")
    assert row.realized_pnl == D("446.25")  # 614.86 − 168.61
    assert row.fees == D("13.00")
    leg = row.legs[0]
    assert leg.right == "P"
    assert leg.side == "short"
    assert leg.strike == D("16")
    assert leg.open_price == D("0.6149")  # 614.86 / (10×100), quantized to price_decimals
    assert leg.close_price == D("0.1686")
    assert leg.closed_on == closed["t1"].date()
    assert leg.contracts == D("10")


# -- account summary ------------------------------------------------------------------


def test_account_summary_splits_options_equity_cash(user, usd, accounts, spy, pcs, marks):
    # 100 SPY shares + cash deposit alongside the PCS.
    book(accounts, usd, ts=OPEN_TS, position_legs=[(spy, 100)], cash="-18000.00")
    with TransactionBuilder(account=accounts["cash"], timestamp=OPEN_TS) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="30000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-30000.00")

    # Counterparty purpose accounts (ADR-0035) are the host's naming
    # convention, so the caller names its own side explicitly.
    summary = account_summary(user, marks, accounts=[accounts["cash"], accounts["holdings"]])
    assert summary.options_value == D("-365.00")  # net short options liability
    assert summary.equity_value == D("18245.00")  # 100 × 182.45
    # cash: 30000 − 18000 + 450.25 − 6.50
    assert summary.cash == D("12443.75")
    assert summary.total_value == summary.cash + summary.equity_value + summary.options_value
    assert summary.options_pnl == D("85.25")
    assert summary.margin_estimate == D("2500")
    assert summary.unpriced == []


# -- calendar / performance / flow ------------------------------------------------------


def test_premium_calendar_days(user, closed):
    days = premium_calendar(user, 2026, 5)
    assert days[datetime.date(2026, 5, 28)].net_premium == D("608.36")  # 614.86 − 6.50 fee
    assert days[datetime.date(2026, 5, 28)].events == 1
    assert days[datetime.date(2026, 5, 28)].wins == 0
    june = premium_calendar(user, 2026, 6)
    day = june[datetime.date(2026, 6, 6)]
    assert day.net_premium == D("-175.11")  # buyback −168.61 − 6.50
    assert day.wins == 1  # the trade closed profitably that day
    assert day.losses == 0


def test_strategy_performance_over_closed_trades(user, closed):
    stats = strategy_performance(user)
    assert stats.total_profit == D("433.25")  # realized 446.25 − fees 13.00
    assert stats.wins == 1
    assert stats.losses == 0
    assert stats.win_ratio == D(1)
    assert stats.average_win == D("433.25")
    assert stats.largest_win == D("433.25")
    assert stats.fees == D("13.00")
    assert stats.strategy_counts == {"short_put": 1}
    assert stats.monthly_profit[datetime.date(2026, 6, 1)] == D("433.25")


def test_pnl_flow_symbol_right_outcome(user, closed, spy):
    flows = pnl_flow(user)
    assert len(flows) == 1
    flow = flows[0]
    assert flow.underlying == spy
    assert flow.right == "P"
    assert flow.realized_pnl == D("433.25")
    assert flow.outcome == "gain"


def test_pnl_flow_summary_totals_and_shares(user, closed, spy):
    from django_assets.trades.reports import pnl_flow_summary

    summary = pnl_flow_summary(user)
    assert summary.total == D("433.25")
    assert summary.by_symbol == {spy: D("433.25")}
    assert summary.by_right == {"P": D("433.25")}
    assert summary.by_outcome == {"gain": D("433.25")}
    assert summary.share_of_total(D("433.25")) == D(1)


def test_closed_legs_carry_pro_rata_fees(user, closed):
    row = closed_option_strategies(user)[0]
    # 6.50 open + 6.50 close, one leg → all of it
    assert row.legs[0].fees == D("13.00")


def test_account_summary_return_pct(user, usd, accounts, spy, pcs, marks):
    with TransactionBuilder(account=accounts["cash"], timestamp=OPEN_TS) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="30000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-30000.00")
    summary = account_summary(user, marks, accounts=[accounts["cash"], accounts["holdings"]])
    assert summary.contributions == D("30000.00")
    expected = (summary.total_value - D("30000.00")) / D("30000.00")
    assert summary.total_return_pct == expected


# -- wheel ---------------------------------------------------------------------------------


def test_wheel_campaigns_adjusted_cost(user, usd, accounts, spy):
    """Reference-shaped campaign: 100 shares at 85.00 with 323.00 of
    option premium collected → adjusted cost 81.77."""
    cc = make_option(usd, spy, strike="90", right="C")
    trade = Trade.objects.create(user=user, name="SPY wheel")
    trade.assign(
        book(accounts, usd, ts=OPEN_TS, position_legs=[(spy, 100)], cash="-8500.00"), fraction=1
    )
    trade.assign(
        book(
            accounts,
            usd,
            ts=OPEN_TS + datetime.timedelta(days=1),
            position_legs=[(cc, -1)],
            cash="323.00",
        ),
        fraction=1,
    )
    trade.add_tag("strategy", "covered_call")

    marks = Marks(
        {
            spy: PriceQuote(
                price=D("78.05"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
            cc: OptionQuote(
                price=D("0.40"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
        }
    )
    campaigns = wheel_campaigns(user, marks)
    assert len(campaigns) == 1
    campaign = campaigns[0]
    assert campaign.underlying == spy
    assert campaign.shares == D("100")
    assert campaign.cost_basis == D("85.00")
    assert campaign.adjusted_cost == D("81.77")
    assert campaign.market_value == D("7805.00")
    assert campaign.pnl_pct == (D("78.05") - D("81.77")) / D("81.77")
    # absolute pnl vs adjusted basis, and the adjusted-cost discount
    assert campaign.pnl == D("7805.00") - D("8177.00")
    assert campaign.adjusted_cost_pct == (D("81.77") - D("85.00")) / D("85.00")

    from django_assets.trades.reports import wheel_total_pnl

    assert wheel_total_pnl(campaigns) == D("7805.00") - D("8177.00")


def test_account_value_series_daily_marks(user, usd, accounts, spy):
    """Cash + positions valued at each session's close; marks carry
    forward across days without a close for an instrument."""
    import io

    from django_assets.core.prices import CSVPriceSource
    from django_assets.trades.reports import account_value_series

    rows = (
        "session,open,high,low,close\n"
        "2026-06-29,100,101,99,100.00\n"
        "2026-06-30,100,103,99,102.00\n"
        "2026-07-02,102,105,101,104.00\n"
    )
    source = CSVPriceSource({spy: io.StringIO(rows)})
    t0 = datetime.datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    with TransactionBuilder(account=accounts["cash"], timestamp=t0) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="20000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-20000.00")
    book(
        accounts,
        usd,
        ts=datetime.datetime(2026, 6, 29, 14, 0, tzinfo=UTC),
        position_legs=[(spy, 100)],
        cash="-10000.00",
    )
    series = account_value_series(
        user,
        source,
        accounts=[accounts["cash"], accounts["holdings"]],
        start=datetime.date(2026, 6, 28),
        end=datetime.date(2026, 7, 2),
    )
    values = dict(series)
    assert values[datetime.date(2026, 6, 29)] == D("10000.00") + D("10000.00")  # cash + 100×100
    assert values[datetime.date(2026, 6, 30)] == D("10000.00") + D("10200.00")
    # 2026-07-01 has no close anywhere: not a session, not emitted
    assert datetime.date(2026, 7, 1) not in values
    assert values[datetime.date(2026, 7, 2)] == D("10000.00") + D("10400.00")
    # the deposit day itself (no market data yet) still appears via cash
    assert values[datetime.date(2026, 6, 28)] == D("20000.00")


def test_classify_trade_live_and_closed(user, usd, accounts, spy, pcs):
    from django_assets.trades.reports import classify_trade

    # live PCS: classify over all legs
    assert classify_trade(pcs["trade"]) == "bull_put_spread"


def test_classify_trade_closed_uses_opening_structure(user, closed):
    from django_assets.trades.reports import classify_trade

    assert classify_trade(closed["trade"]) == "short_put"


def test_same_timestamp_opens_merge_into_one_cohort(user, usd, accounts, spy):
    """A combo booked as per-leg fills at the SAME timestamp (broker
    reality: one combo order, per-leg prices) is ONE cohort: combined
    opening premium, per-leg prices derivable in history."""
    short_put = make_option(usd, spy, strike="190", right="P")
    long_put = make_option(usd, spy, strike="185", right="P")
    trade = Trade.objects.create(user=user, name="per-leg pcs")
    trade.assign(
        book(accounts, usd, ts=OPEN_TS, position_legs=[(short_put, -5)], cash="1075.00"),
        fraction=1,
    )
    trade.assign(
        book(accounts, usd, ts=OPEN_TS, position_legs=[(long_put, 5)], cash="-710.00"),
        fraction=1,
    )
    close_ts = OPEN_TS + datetime.timedelta(days=5)
    trade.assign(
        book(accounts, usd, ts=close_ts, position_legs=[(short_put, 5)], cash="-500.00"),
        fraction=1,
    )
    trade.assign(
        book(accounts, usd, ts=close_ts, position_legs=[(long_put, -5)], cash="300.00"),
        fraction=1,
    )
    rows = closed_option_strategies(user)
    row = next(r for r in rows if r.trade == trade)
    assert row.initial_premium == D("365.00")  # 1075 − 710, one cohort
    assert row.realized_pnl == D("165.00")
    legs = {leg.strike: leg for leg in row.legs}
    assert legs[D("190")].open_price == D("2.15")  # 1075 / 500
    assert legs[D("190")].close_price == D("1.0000")
    assert legs[D("185")].open_price == D("1.42")
    assert legs[D("185")].close_price == D("0.60")


def test_open_report_roll_inclusive_pnl(user, rolled, usd, spy):
    marks = Marks(
        {
            spy: PriceQuote(
                price=D("57.20"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
            rolled["b"]: OptionQuote(
                price=D("1.00"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            ),
        }
    )
    row = next(r for r in open_option_strategies(user, marks) if r.trade == rolled["trade"])
    # live: premium 530.06, cost to close 500 → unrealized 30.06
    assert row.unrealized_pnl == D("30.06")
    # roll-inclusive: (30.06 + 400.67) / premium_incl_rolls
    assert row.pnl_pct_incl_rolls == (D("30.06") + D("400.67")) / row.premium_incl_rolls


def test_performance_weekly_buckets_and_symbol_filter(user, closed, spy):
    stats = strategy_performance(user, underlyings=["SPY"])
    assert stats.total_profit == D("433.25")
    empty = strategy_performance(user, underlyings=["ZZZ"])
    assert empty.total_profit == D(0)
    week_start = datetime.date(2026, 6, 1)  # Monday of the close week
    assert strategy_performance(user).weekly_profit[week_start] == D("433.25")


def test_closed_strategy_assigned_flag(user, usd, accounts, spy):
    csp = make_option(usd, spy, strike="60", right="P", expiry=datetime.date(2026, 6, 19))
    t0 = datetime.datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    trade = Trade.objects.create(user=user, name="assigned csp")
    trade.assign(book(accounts, usd, ts=t0, position_legs=[(csp, -1)], cash="120.00"), fraction=1)
    # assignment: option goes to zero, shares arrive, strike cash leaves
    trade.assign(
        book(
            accounts,
            usd,
            ts=datetime.datetime(2026, 6, 19, 20, 0, tzinfo=UTC),
            position_legs=[(csp, 1), (spy, 100)],
            cash="-6000.00",
        ),
        fraction=1,
    )
    rows = closed_option_strategies(user)
    row = next(r for r in rows if r.trade == trade)
    assert row.assigned is True
    # The strike cash is the SHARES' basis (calculate_pnl's assignment
    # policy), not an option loss: realized = the premium kept.
    assert row.realized_pnl == D("120.00")
    # And the history label is the option structure, not "stock".
    assert row.strategy == "short_put"
    leg = row.legs[0]
    # No closing FILL exists: the strike cash is not an option price.
    assert leg.close_price is None
    assert leg.status == "assigned"

    from django_assets.trades.reports import assignments, wheel_campaigns

    rows_a = assignments(user)
    assert len(rows_a) == 1
    a = rows_a[0]
    assert a.underlying == spy
    assert a.shares == D("100")
    assert a.strike == D("60")
    assert a.right == "P"
    assert a.assigned_on == datetime.date(2026, 6, 19)

    # The assignment's strike cash is the campaign's share basis.
    campaign = next(c for c in wheel_campaigns(user, Marks({})) if c.trade == trade)
    assert campaign.cost_basis == D("60.00")
    assert campaign.shares == D("100")
    # premium collected adjusts: (6000 − 120) / 100
    assert campaign.adjusted_cost == D("58.80")


def test_closed_leg_expired_status(user, usd, accounts, spy):
    csp = make_option(usd, spy, strike="10", right="P", expiry=datetime.date(2026, 6, 19))
    trade = Trade.objects.create(user=user, name="expired csp")
    trade.assign(
        book(
            accounts,
            usd,
            ts=datetime.datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
            position_legs=[(csp, -3)],
            cash="90.00",
        ),
        fraction=1,
    )
    trade.assign(
        book(
            accounts,
            usd,
            ts=datetime.datetime(2026, 6, 19, 20, 0, tzinfo=UTC),
            position_legs=[(csp, 3)],
        ),
        fraction=1,
    )
    row = next(r for r in closed_option_strategies(user) if r.trade == trade)
    assert row.legs[0].status == "expired"  # zero-cash close on expiry day


def test_open_strategy_extrinsic_value(user, pcs, marks, spy, usd):
    # marks fixture has no extrinsic; rebuild quotes with it
    quotes = dict(marks.quotes)
    short_quote = quotes[pcs["short"]]
    from dataclasses import replace

    quotes[pcs["short"]] = replace(short_quote, extrinsic_value=D("0.37"))
    quotes[pcs["long"]] = replace(quotes[pcs["long"]], extrinsic_value=D("1.42"))
    row = open_option_strategies(user, Marks(quotes))[0]
    # Σ signed qty × extrinsic × multiplier: −5×0.37×100 + 5×1.42×100
    assert row.extrinsic_value == D("525.00")


def test_wheel_campaign_history_rows(user, usd, accounts, spy):
    cc = make_option(usd, spy, strike="90", right="C")
    old_cc = make_option(usd, spy, strike="85", right="C", expiry=datetime.date(2026, 6, 19))
    trade = Trade.objects.create(user=user, name="wheel hist")
    t0 = datetime.datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    trade.assign(
        book(accounts, usd, ts=t0, position_legs=[(spy, 100)], cash="-8500.00"), fraction=1
    )
    trade.assign(
        book(accounts, usd, ts=t0, position_legs=[(old_cc, -1)], cash="120.00"), fraction=1
    )
    trade.assign(
        book(
            accounts,
            usd,
            ts=datetime.datetime(2026, 6, 19, 20, 0, tzinfo=UTC),
            position_legs=[(old_cc, 1)],
        ),
        fraction=1,
    )
    trade.assign(
        book(
            accounts,
            usd,
            ts=datetime.datetime(2026, 6, 22, 14, 0, tzinfo=UTC),
            position_legs=[(cc, -1)],
            cash="323.00",
        ),
        fraction=1,
    )
    campaign = next(c for c in wheel_campaigns(user, Marks({})) if c.trade == trade)
    assert campaign.total_premium == D("443.00")  # 120 + 323
    history = campaign.history
    assert len(history) == 2
    expired = next(h for h in history if h.instrument == old_cc)
    assert expired.status == "expired"
    assert expired.initial_premium == D("120.00")
    assert expired.realized_pnl == D("120.00")
    live = next(h for h in history if h.instrument == cc)
    assert live.status == "open"
    assert live.initial_premium == D("323.00")


def test_equity_holdings_report(user, usd, accounts, spy):
    from django_assets.trades.reports import equity_holdings

    book(accounts, usd, ts=OPEN_TS, position_legs=[(spy, 100)], cash="-8500.00")
    marks = Marks(
        {
            spy: PriceQuote(
                price=D("90.00"), currency=usd, as_of=None, source="t", kind=PriceKind.EOD
            )
        }
    )
    rows = equity_holdings(user, marks, accounts=[accounts["cash"], accounts["holdings"]])
    assert len(rows) == 1
    holding = rows[0]
    assert holding.instrument == spy
    assert holding.shares == D("100")
    assert holding.cost_basis == D("85.00")
    assert holding.market_value == D("9000.00")
    assert holding.pnl_pct == (D("90.00") - D("85.00")) / D("85.00")
