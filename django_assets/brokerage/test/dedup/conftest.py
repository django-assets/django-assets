"""Fixtures for the B7 dedup-proposal tests (ADR-0029)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.test.imports.conftest import (  # noqa: F401
    aapl,
    accounts,
    batch,
    usd,
    user,
)
from django_assets.core.builder import TransactionBuilder

D = Decimal

# Matches the Buy row in SCHWAB_CSV: 03/10/2026, 10 AAPL, net -1755.55.
BUY_DATE = datetime.datetime(2026, 3, 10, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def manual_buy(accounts, usd, aapl):  # noqa: F811
    """The user's earlier hand-entered version of the same purchase."""
    with TransactionBuilder(
        account=accounts["cash"], timestamp=BUY_DATE, description="bought apple"
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1755.55")
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount="10")
        b.add_leg(account=accounts["external"], instrument=usd, amount="1755.55")
        b.add_leg(account=accounts["external"], instrument=aapl, amount="-10")
    return b.transaction
