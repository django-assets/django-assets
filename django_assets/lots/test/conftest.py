"""Shared fixtures for lots tests: a plain equity ledger."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Instrument
from django_assets.instruments.equities import templates as eq

D = Decimal
TS = datetime.datetime(2026, 3, 2, 20, 0, tzinfo=datetime.UTC)


def at(days: int, hours: int = 0) -> datetime.datetime:
    return TS + datetime.timedelta(days=days, hours=hours)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="taxpayer", password="x")


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
    names = ["cash", "holdings", "external", "commissions", "regulatory_fees"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


@pytest.fixture
def buy(accounts, aapl):
    def _buy(quantity, price, when, commission="0", instrument=None, **kwargs):
        return eq.buy_shares(
            accounts=accounts,
            instrument=instrument or aapl,
            quantity=quantity,
            price=price,
            commission=commission,
            timestamp=when,
            **kwargs,
        )

    return _buy


@pytest.fixture
def sell(accounts, aapl):
    def _sell(quantity, price, when, commission="0", instrument=None, **kwargs):
        return eq.sell_shares(
            accounts=accounts,
            instrument=instrument or aapl,
            quantity=quantity,
            price=price,
            commission=commission,
            timestamp=when,
            **kwargs,
        )

    return _sell
