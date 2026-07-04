"""Fixtures for the wave-1 plumbing template goldens (B3)."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.core.models import Instrument

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def accounts(user):
    return ensure_standard_accounts(user)


def legs_by(tx):
    result = {}
    for leg in tx.legs.select_related("account", "instrument"):
        key = (leg.account.name, leg.instrument.code)
        assert key not in result, f"duplicate leg {key}"
        result[key] = leg.amount
    return result
