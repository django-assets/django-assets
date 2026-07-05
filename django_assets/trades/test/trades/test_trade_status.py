"""T2: derived status/position/dates from allocations (spec §3)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account
from django_assets.trades.models import Trade

from .conftest import TS

pytestmark = pytest.mark.ledger

D = Decimal


def make_trade_event(accounts, usd, aapl, quantity, cash, timestamp):
    with TransactionBuilder(account=accounts["cash"], timestamp=timestamp) as b:
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount=quantity)
        b.add_leg(account=accounts["market"], instrument=aapl, amount=-D(quantity))
        b.add_leg(account=accounts["cash"], instrument=usd, amount=cash)
        b.add_leg(account=accounts["market"], instrument=usd, amount=-D(cash))
    return b.transaction


def test_lifecycle_open_adjust_close(user, accounts, usd, aapl):
    """0→100 opens, +50 adjusts, 150→0 closes; dates key on settlement."""
    t1 = TS
    t2 = TS + datetime.timedelta(days=1)
    t3 = TS + datetime.timedelta(days=5)
    open_tx = make_trade_event(accounts, usd, aapl, "100", "-10000.00", t1)
    add_tx = make_trade_event(accounts, usd, aapl, "50", "-5100.00", t2)
    close_tx = make_trade_event(accounts, usd, aapl, "-150", "15600.00", t3)

    trade = Trade.objects.create(user=user, name="swing")
    trade.assign(open_tx, quantity="100", instrument=aapl)
    assert trade.status == "open"
    assert trade.open_date == t1
    assert trade.closed_date is None

    trade.assign(add_tx, quantity="50", instrument=aapl)
    assert trade.status == "open"

    trade.assign(close_tx, quantity="150", instrument=aapl)
    assert trade.status == "closed"
    assert trade.closed_date == t3
    assert trade.net_position(aapl) == D("0")


def test_partial_close_across_two_trades(user, accounts, usd, aapl):
    open_tx = make_trade_event(accounts, usd, aapl, "1000", "-100000.00", TS)
    close_tx = make_trade_event(
        accounts, usd, aapl, "-400", "44000.00", TS + datetime.timedelta(days=2)
    )
    a = Trade.objects.create(user=user, name="A")
    b = Trade.objects.create(user=user, name="B")
    a.assign(open_tx, quantity="600", instrument=aapl)
    b.assign(open_tx, quantity="400", instrument=aapl)
    b.assign(close_tx, quantity="400", instrument=aapl)
    assert a.status == "open"
    assert a.net_position(aapl) == D("600")
    assert b.status == "closed"
    assert b.net_position(aapl) == D("0")


def test_short_lifecycle(user, sale_tx, aapl):
    """Negative allocated net is an OPEN (short) position."""
    trade = Trade.objects.create(user=user, name="short")
    trade.assign(sale_tx, quantity="1000", instrument=aapl)
    assert trade.status == "open"
    assert trade.net_position(aapl) == D("-1000")


def test_multi_account_trade(user, accounts, usd, aapl):
    """Open in account A, close in account B — first-class."""
    ira = Account.objects.create(owner=user, name="ira_holdings")
    open_tx = make_trade_event(accounts, usd, aapl, "10", "-1000.00", TS)
    with TransactionBuilder(
        account=accounts["cash"], timestamp=TS + datetime.timedelta(days=3)
    ) as b:
        b.add_leg(account=ira, instrument=aapl, amount="-10")
        b.add_leg(account=accounts["market"], instrument=aapl, amount="10")
        b.add_leg(account=accounts["cash"], instrument=usd, amount="1100.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-1100.00")
    close_tx = b.transaction

    trade = Trade.objects.create(user=user, name="moved")
    trade.assign(open_tx, quantity="10", instrument=aapl)
    trade.assign(close_tx, quantity="10", instrument=aapl)
    assert trade.status == "closed"
    assert set(trade.accounts_involved()) >= {accounts["holdings"].pk, ira.pk}


def test_tracked_instruments_and_override(user, accounts, usd, aapl):
    open_tx = make_trade_event(accounts, usd, aapl, "100", "-10000.00", TS)
    trade = Trade.objects.create(user=user, name="tracked")
    trade.assign(open_tx, quantity="100", instrument=aapl)
    assert trade.tracked_instruments() == [aapl]  # cash roles excluded
    # Pure-cash view: explicit override flips the perspective.
    assert trade.status_for(instruments=[usd]) == "open"


def test_hierarchy(user, accounts, usd, aapl):
    parent = Trade.objects.create(user=user, name="campaign")
    child = Trade.objects.create(user=user, name="leg1", parent=parent)
    open_tx = make_trade_event(accounts, usd, aapl, "100", "-10000.00", TS)
    child.assign(open_tx, quantity="100", instrument=aapl)
    assert parent.net_position(aapl) == D("100")  # aggregates descendants
    assert parent.status == "open"

    from django.core.exceptions import ValidationError

    parent.parent = child  # cycle
    with pytest.raises(ValidationError):
        parent.clean()

    from django.contrib.auth import get_user_model

    stranger = get_user_model().objects.create_user(username="stranger", password="x")
    foreign = Trade.objects.create(user=stranger, name="not yours")
    child.parent = foreign
    with pytest.raises(ValidationError):
        child.clean()
