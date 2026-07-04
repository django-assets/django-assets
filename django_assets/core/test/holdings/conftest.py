"""Scripted ledger history for the C5 query-API tests.

Timeline (all UTC; 2026-03-10 is a Tuesday — the ADR-0012 T+1 example):

  Mon 03-09 12:00  deposit   +10000.00 USD into cash
  Tue 03-10 15:30  (trade)   buy 100 AAPL for 8000 USD — executes Tuesday...
  Wed 03-11 13:30  (settle)  ...settles Wednesday: timestamp = settlement
  Thu 03-12 14:00  sell 40 AAPL for 4000 USD
  Fri 03-13 10:00  buy 0.5 BTC for 3000 USD
  Fri 03-13 18:00  sell 0.5 BTC for 3100 USD  → BTC position returns to zero
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Instrument

D = Decimal
UTC = datetime.UTC

DEPOSIT_TS = datetime.datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
TRADE_TS = datetime.datetime(2026, 3, 10, 15, 30, tzinfo=UTC)  # Tuesday
SETTLE_TS = datetime.datetime(2026, 3, 11, 13, 30, tzinfo=UTC)  # Wednesday
SELL_TS = datetime.datetime(2026, 3, 12, 14, 0, tzinfo=UTC)
BTC_BUY_TS = datetime.datetime(2026, 3, 13, 10, 0, tzinfo=UTC)
BTC_SELL_TS = datetime.datetime(2026, 3, 13, 18, 0, tzinfo=UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2, price_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def btc(usd):
    return Instrument.objects.create(
        code="BTC", quantity_decimals=8, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "external"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


def swap(builder, from_account, to_account, give_inst, give_amt, get_inst, get_amt):
    builder.add_leg(account=from_account, instrument=give_inst, amount=f"-{give_amt}")
    builder.add_leg(account=to_account, instrument=get_inst, amount=get_amt)
    external = Account.objects.get(name="external", owner=from_account.owner)
    builder.add_leg(account=external, instrument=give_inst, amount=give_amt)
    builder.add_leg(account=external, instrument=get_inst, amount=f"-{get_amt}")


@pytest.fixture
def history(accounts, usd, aapl, btc):
    cash, holdings, external = accounts["cash"], accounts["holdings"], accounts["external"]
    with TransactionBuilder(account=cash, timestamp=DEPOSIT_TS, description="deposit") as b:
        b.add_leg(account=cash, instrument=usd, amount="10000.00")
        b.add_leg(account=external, instrument=usd, amount="-10000.00")
    with TransactionBuilder(
        account=cash, timestamp=SETTLE_TS, trade_timestamp=TRADE_TS, description="buy AAPL"
    ) as b:
        swap(b, cash, holdings, usd, "8000.00", aapl, "100")
    with TransactionBuilder(account=cash, timestamp=SELL_TS, description="sell AAPL") as b:
        swap(b, holdings, cash, aapl, "40", usd, "4000.00")
    with TransactionBuilder(account=cash, timestamp=BTC_BUY_TS, description="buy BTC") as b:
        swap(b, cash, holdings, usd, "3000.00", btc, "0.50000000")
    with TransactionBuilder(account=cash, timestamp=BTC_SELL_TS, description="sell BTC") as b:
        swap(b, holdings, cash, btc, "0.50000000", usd, "3100.00")
    return accounts
