"""Shared fixtures for the equity template golden-leg tests (I2)."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

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
        code="AAPL", quantity_decimals=0, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def accounts(user):
    """The documented routing-key convention (brokerage's
    ensure_standard_accounts produces the same dict shape)."""
    names = [
        "cash",
        "holdings",
        "market",
        "funding",
        "issuers",
        "conversions",
        "commissions",
        "regulatory_fees",
        "tax_withheld",
        "foreign_tax",
    ]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


def legs_by(tx):
    """(account_name, instrument_code) -> amount, for golden comparisons.
    Merges multiple legs on the same pair by summing (none expected)."""
    result = {}
    for leg in tx.legs.select_related("account", "instrument"):
        key = (leg.account.name, leg.instrument.code)
        assert key not in result, f"duplicate leg {key}"
        result[key] = leg.amount
    return result
