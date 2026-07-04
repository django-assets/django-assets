import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Instrument, Transaction

TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def eur():
    return Instrument.objects.create(code="EUR", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(code="AAPL", quantity_decimals=0, price_currency=usd)


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "external", "commissions", "fees", "eur_cash"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


@pytest.fixture
def make_tx(accounts):
    def _make(description="tx", account_name="cash", timestamp=TS):
        return Transaction.objects.create(
            account=accounts[account_name], timestamp=timestamp, description=description
        )

    return _make


D = Decimal
