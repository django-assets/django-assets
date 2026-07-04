"""T2: the default pro-rata allocator (trades spec §4, ADR-0030 §3)."""

from decimal import Decimal

import pytest

from django_assets.core.builder import TransactionBuilder
from django_assets.trades.exceptions import OverAllocationError
from django_assets.trades.models import Trade

from ..harness import inviolable
from .conftest import TS

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def buy_tx(accounts, usd, aapl, user):
    """Buy 1000 AAPL for $175,500 + $2 commission (HIMS decomposition)."""
    from django_assets.core.models import Account

    commissions = Account.objects.create(owner=user, name="commissions")
    with TransactionBuilder(account=accounts["cash"], timestamp=TS) as b:
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount="1000")
        b.add_leg(account=accounts["external"], instrument=aapl, amount="-1000")
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-175502.00")
        b.add_leg(account=commissions, instrument=usd, amount="2.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="175500.00")
    return b.transaction


def test_assign_quantity_pro_rates_cash(user, buy_tx, aapl, usd, accounts):
    """500 of 1000 shares → 50% of principal (cost) and commission (fee);
    counterparty mirror legs are never allocated."""
    trade = Trade.objects.create(user=user, name="half")
    with inviolable():
        allocations = trade.assign(buy_tx, quantity="500", instrument=aapl)

    by_category = {(a.category, a.leg.account.name): a.amount for a in allocations}
    assert by_category[("", "holdings")] == D("500")
    assert by_category[("cost", "cash")] == D("-87751.00")
    assert by_category[("fee", "commissions")] == D("1.00")
    assert not any(a.leg.account.name == "external" for a in allocations)
    assert trade.net_position(aapl) == D("500")


def test_sale_cash_is_revenue(user, sale_tx, aapl, usd):
    trade = Trade.objects.create(user=user, name="sale half")
    allocations = trade.assign(sale_tx, quantity="500", instrument=aapl)
    categories = {a.category for a in allocations}
    assert "revenue" in categories
    assert trade.net_position(aapl) == D("-500")


def test_fraction_variant(user, buy_tx, aapl):
    trade = Trade.objects.create(user=user, name="quarter")
    trade.assign(buy_tx, fraction="0.25")
    assert trade.net_position(aapl) == D("250")


def test_final_slice_absorbs_rounding(user, sale_tx, aapl, usd, accounts):
    """100.1/899.9: the closing assign takes exact remainders so the cash
    slices sum to the full leg amount."""
    first = Trade.objects.create(user=user, name="first")
    second = Trade.objects.create(user=user, name="second")
    first.assign(sale_tx, quantity="100.1", instrument=aapl)
    second.assign(sale_tx, quantity="899.9", instrument=aapl)

    cash_leg = sale_tx.legs.get(account=accounts["cash"])
    from django_assets.trades.models import TradeAllocation

    total_cash = sum(
        a.amount for a in TradeAllocation.objects.filter(leg=cash_leg)
    )
    assert total_cash == cash_leg.amount  # exact, no rounding residue
    assert first.net_position(aapl) + second.net_position(aapl) == D("-1000")


def test_assign_respects_partition(user, sale_tx, aapl):
    first = Trade.objects.create(user=user, name="most")
    first.assign(sale_tx, quantity="900", instrument=aapl)
    second = Trade.objects.create(user=user, name="too much")
    with pytest.raises(OverAllocationError):
        second.assign(sale_tx, quantity="200", instrument=aapl)
