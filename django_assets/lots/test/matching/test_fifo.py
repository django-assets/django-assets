"""L2: FIFO matching + realized gains (lots spec §2.2, ADR-0032 §1/§2)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.lots.models import Lot, LotMatch
from django_assets.lots.rebuild import rebuild_lots

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


def test_fifo_order_and_partial_consumption(accounts, aapl, buy, sell):
    buy("100", "10.00", at(0))
    buy("100", "20.00", at(1))
    sell("150", "25.00", at(30))
    rebuild_lots(accounts["holdings"])

    first, second = Lot.objects.order_by("acquired_at")
    assert first.quantity_remaining == D("0")
    assert first.cost_basis_remaining == D("0")
    assert second.quantity_remaining == D("50")
    assert second.cost_basis_remaining == D("1000.00")

    matches = LotMatch.objects.order_by("lot__acquired_at")
    assert [m.quantity for m in matches] == [D("100"), D("50")]
    assert matches[0].realized_gain == D("1500.00")  # 2500 − 1000
    assert matches[1].realized_gain == D("250.00")  # 1250 − 1000


def test_term_classification_boundary(accounts, aapl, buy, sell):
    """One year exactly = short; one year plus a day = long."""
    buy("10", "10.00", at(0))
    sell("5", "20.00", at(365))
    sell("5", "20.00", at(366))
    rebuild_lots(accounts["holdings"])
    exact, plus_one = LotMatch.objects.order_by("closing_leg__transaction__timestamp")
    assert exact.term == "short"
    assert plus_one.term == "long"


def test_fees_no_double_counting(accounts, aapl, buy, sell):
    """Buy fees capitalize into basis; sale fees net out of proceeds —
    each exactly once."""
    buy("100", "10.00", at(0), commission="2.00")  # basis 1002
    sell("100", "12.00", at(10), commission="3.00")  # proceeds 1197
    rebuild_lots(accounts["holdings"])
    match = LotMatch.objects.get()
    assert match.basis_recovered == D("1002.00")
    assert match.proceeds == D("1197.00")
    assert match.realized_gain == D("195.00")


def test_full_roundtrip_conservation(accounts, aapl, buy, sell):
    buy("100", "10.00", at(0), commission="1.00")
    buy("60", "11.00", at(1))
    sell("160", "12.00", at(40), commission="1.50")
    rebuild_lots(accounts["holdings"])
    total_realized = sum(m.realized_gain for m in LotMatch.objects.all())
    # Net attributable cash: −1001 − 660 + 1918.50 = 257.50
    assert total_realized == D("257.50")
    assert all(lot.quantity_remaining == 0 for lot in Lot.objects.all())


def test_short_direction_lots(accounts, aapl, buy, sell):
    """Open-by-sale lot with proceeds-as-opening; closing purchase
    matches against it."""
    sell("100", "20.00", at(0))
    buy("100", "15.00", at(10))
    rebuild_lots(accounts["holdings"])
    lot = Lot.objects.get()
    assert lot.direction == "short"
    assert lot.quantity_remaining == D("0")
    match = LotMatch.objects.get()
    assert match.realized_gain == D("500.00")  # 2000 proceeds − 1500 cover


def test_option_multiplier_in_cash_attribution(accounts, usd):
    from django_assets.core.models import Instrument
    from django_assets.instruments.options import templates as opt
    from django_assets.instruments.options.models import OptionMeta

    call = Instrument.objects.create(
        code="AAPL C200",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    underlying = Instrument.objects.create(code="AAPL_U", quantity_decimals=0)
    OptionMeta.objects.create(
        instrument=call,
        underlying=underlying,
        expiry=datetime.date(2026, 12, 18),
        strike=D("200"),
        right="C",
    )
    opt.buy_option(
        accounts=accounts, instrument=call, contracts="2", price="5.00", timestamp=at(0)
    )
    opt.sell_option(
        accounts=accounts, instrument=call, contracts="2", price="7.00", timestamp=at(5)
    )
    rebuild_lots(accounts["holdings"])
    match = LotMatch.objects.get()
    assert match.basis_recovered == D("1000.00")  # 2 × 5 × 100
    assert match.realized_gain == D("400.00")


def test_divergence_from_trades_view(accounts, aapl, buy, sell, user):
    """The ADR-0020 two-true-stories guard: lots FIFO vs trades
    average-cost legitimately differ on the same activity."""
    tx1 = buy("100", "10.00", at(0))
    tx2 = buy("100", "20.00", at(1))
    tx3 = sell("100", "20.00", at(400))
    rebuild_lots(accounts["holdings"])
    fifo_realized = sum(m.realized_gain for m in LotMatch.objects.all())
    assert fifo_realized == D("1000.00")  # first (cheap) lot out, long-term

    from django_assets.trades.models import Trade

    trade = Trade.objects.create(user=user, name="same activity")
    for tx in (tx1, tx2, tx3):
        trade.assign(tx, fraction="1")
    assert trade.calculate_pnl()["realized_pnl"] == D("500.00")  # average cost
    assert fifo_realized != trade.calculate_pnl()["realized_pnl"]
