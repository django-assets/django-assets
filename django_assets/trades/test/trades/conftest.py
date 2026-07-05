"""Fixtures for the trades T1 spine tests."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Instrument

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=4, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "market", "funding", "issuers", "conversions"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


@pytest.fixture
def sale_tx(accounts, usd, aapl):
    """The ADR-0030 use case: sell 1000 AAPL for $200,000."""
    with TransactionBuilder(
        account=accounts["cash"], timestamp=TS, description="sell 1000 AAPL"
    ) as b:
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount="-1000")
        b.add_leg(account=accounts["market"], instrument=aapl, amount="1000")
        b.add_leg(account=accounts["cash"], instrument=usd, amount="200000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-200000.00")
    return b.transaction


@pytest.fixture
def sale_leg(sale_tx, accounts, aapl):
    return sale_tx.legs.get(account=accounts["holdings"], instrument=aapl)
