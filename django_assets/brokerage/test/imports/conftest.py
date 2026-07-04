"""Fixtures for the B4 import-management tests."""

import datetime

import pytest
from django.contrib.auth import get_user_model

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.models import AccountProfile, ImportBatch
from django_assets.core.models import Identifier, Instrument

TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)

SCHWAB_CSV = """\
"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"03/10/2026","Buy","AAPL","APPLE INC","10","175.50","0.55","-1755.55"
"03/11/2026","Sell","AAPL","APPLE INC","4","180.00","0.52","719.48"
"03/12/2026","Journal","","TRANSFER FUNDS","","","","500.00"
"""


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    inst = Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=4, price_currency=usd
    )
    Identifier.objects.create(instrument=inst, type="ticker", value="AAPL")
    return inst


@pytest.fixture
def accounts(user):
    accounts = ensure_standard_accounts(user)
    AccountProfile.objects.create(account=accounts["cash"], allows_reconciliation=True)
    AccountProfile.objects.create(account=accounts["holdings"], allows_reconciliation=True)
    return accounts


@pytest.fixture
def batch(accounts):
    return ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="trades",
        schema_format_kind="csv",
        schema_version="2026.1",
        period_start=datetime.date(2026, 3, 1),
        period_end=datetime.date(2026, 3, 31),
        file_name="trades-march.csv",
        file_hash="abc123",
    )
