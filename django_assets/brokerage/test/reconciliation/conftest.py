"""Fixtures for B6 reconciliation tests: one processed Schwab batch."""

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.test.imports.conftest import (  # noqa: F401
    SCHWAB_CSV,
    accounts,
    aapl,
    batch,
    usd,
    user,
)


@pytest.fixture
def processed(batch, accounts, usd, aapl):  # noqa: F811
    process_batch(batch, SCHWAB_CSV)
    return batch
