"""T4: P&L (trades spec §4, ADR-0030 §3, ADR-0020 golden).

Realized is computed from revenue/cost cash allocations by average-cost
event walk — never stored. Fee-category slices are reported in the
summary but not re-subtracted (our allocator's cash slices are the
perspective account's NET amounts; fees already reduced them).
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.models import Account, Instrument
from django_assets.core.prices import StaticPriceSource
from django_assets.instruments.options import templates as option_templates
from django_assets.trades.models import Trade

from ..trades.conftest import TS

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def routing(user, accounts):
    commissions = Account.objects.create(owner=user, name="commissions")
    regulatory = Account.objects.create(owner=user, name="regulatory_fees")
    return {**accounts, "commissions": commissions, "regulatory_fees": regulatory}


@pytest.fixture
def hims_call(usd):
    hims = Instrument.objects.create(
        code="HIMS", quantity_decimals=0, price_decimals=4, price_currency=usd
    )
    option = Instrument.objects.create(
        code="HIMS 261218C00030000",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    from django_assets.instruments.options.models import OptionMeta

    OptionMeta.objects.create(
        instrument=option,
        underlying=hims,
        expiry=datetime.date(2026, 12, 18),
        strike=D("30"),
        right="C",
    )
    return option


def test_hims_golden_realized(user, routing, usd, hims_call):
    """ADR-0020: sell 2 for net $1,569.04, buy back for $1,000 → $569.04,
    computed from allocations."""
    sell = option_templates.sell_option(
        accounts=routing,
        instrument=hims_call,
        contracts="2",
        price="7.85",
        commission="0.90",
        regulatory_fee="0.06",
        timestamp=TS,
    )
    buy_back = option_templates.buy_option(
        accounts=routing,
        instrument=hims_call,
        contracts="2",
        price="5.00",
        timestamp=TS + datetime.timedelta(days=20),
    )
    trade = Trade.objects.create(user=user, name="HIMS earnings swing")
    trade.assign(sell, quantity="2", instrument=hims_call)
    trade.assign(buy_back, quantity="2", instrument=hims_call)

    pnl = trade.calculate_pnl()
    assert pnl["realized_pnl"] == D("569.04")
    assert pnl["unrealized_pnl"] is None  # closed; nothing to mark
    assert pnl["total_pnl"] == D("569.04")
    assert pnl["transactions_count"] == 2
    assert trade.status == "closed"


def test_split_trade_pnl_sums_exactly(user, sale_tx, aapl):
    """500/500 of the $200k sale: pro-rata proceeds; the parts sum to the
    whole-position number exactly (conservation)."""
    a = Trade.objects.create(user=user, name="A")
    b = Trade.objects.create(user=user, name="B")
    a.assign(sale_tx, quantity="500", instrument=aapl)
    b.assign(sale_tx, quantity="500", instrument=aapl)

    pnl_a = a.calculate_pnl()
    pnl_b = b.calculate_pnl()
    # Short positions open: realized 0 (proceeds mark the short's entry).
    assert pnl_a["realized_pnl"] + pnl_b["realized_pnl"] == D("0")
    assert pnl_a["cost_basis"] + pnl_b["cost_basis"] == D("-200000.00")


def test_parent_aggregates_and_as_of(user, routing, usd, hims_call):
    sell = option_templates.sell_option(
        accounts=routing, instrument=hims_call, contracts="2", price="7.85", timestamp=TS
    )
    buy_back = option_templates.buy_option(
        accounts=routing,
        instrument=hims_call,
        contracts="2",
        price="5.00",
        timestamp=TS + datetime.timedelta(days=20),
    )
    parent = Trade.objects.create(user=user, name="campaign")
    child = Trade.objects.create(user=user, name="leg", parent=parent)
    child.assign(sell, quantity="2", instrument=hims_call)
    child.assign(buy_back, quantity="2", instrument=hims_call)

    assert parent.calculate_pnl()["realized_pnl"] == D("570.00")  # no fees this time
    # Before the buy-back the short was open: nothing realized yet.
    early = parent.calculate_pnl(as_of=TS + datetime.timedelta(days=1))
    assert early["realized_pnl"] == D("0")


def test_unrealized_with_price_source(user, accounts, usd, aapl, sale_tx):
    """Open short of 1000 AAPL from $200k; marked at $195 → +$5,000."""
    trade = Trade.objects.create(user=user, name="short aapl")
    trade.assign(sale_tx, quantity="1000", instrument=aapl)

    pnl = trade.calculate_pnl(price_source=StaticPriceSource({aapl: "195.00"}))
    assert pnl["unrealized_pnl"] == D("5000.00")
    assert pnl["current_value"] == D("-195000.00")
    assert pnl["total_pnl"] == D("5000.00")

    bare = trade.calculate_pnl()
    assert bare["unrealized_pnl"] is None  # no source, no guess
    assert bare["unpriced"] == [aapl]


def test_divergence_from_naive_fifo(user, accounts, usd, aapl):
    """Two true stories: trades' average-cost number differs from naive
    FIFO for the same activity — the documented claim, guarded."""
    from django_assets.core.builder import TransactionBuilder

    def buy(qty, cash, ts):
        with TransactionBuilder(account=accounts["cash"], timestamp=ts) as b:
            b.add_leg(account=accounts["holdings"], instrument=aapl, amount=qty)
            b.add_leg(account=accounts["external"], instrument=aapl, amount=-D(qty))
            b.add_leg(account=accounts["cash"], instrument=usd, amount=cash)
            b.add_leg(account=accounts["external"], instrument=usd, amount=-D(cash))
        return b.transaction

    tx1 = buy("100", "-1000.00", TS)
    tx2 = buy("100", "-2000.00", TS + datetime.timedelta(days=1))
    tx3 = buy("-100", "2000.00", TS + datetime.timedelta(days=2))

    trade = Trade.objects.create(user=user, name="avg vs fifo")
    for tx in (tx1, tx2, tx3):
        trade.assign(tx, fraction="1")

    average_cost_realized = trade.calculate_pnl()["realized_pnl"]
    assert average_cost_realized == D("500.00")  # 2000 − 100×avg(15)
    naive_fifo_realized = D("2000.00") - D("1000.00")  # first lot out
    assert average_cost_realized != naive_fifo_realized
