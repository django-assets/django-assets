"""The library's shipped conformance suite (ADR-0039 §8), run against
the connector for both asset classes — the same bar every PriceSource
implementation is held to."""

import pytest
from freezegun import freeze_time

from django_assets.core.prices_conformance import PriceSourceConformance

from .conftest import FROZEN


@pytest.fixture(autouse=True)
def _frozen_market_open():
    with freeze_time(FROZEN):
        yield


@pytest.fixture
def unpriceable(usd):
    return usd


class TestMarketDataStockConformance(PriceSourceConformance):
    @pytest.fixture
    def priced(self, acme):
        return acme


class TestMarketDataOptionConformance(PriceSourceConformance):
    @pytest.fixture
    def priced(self, acme_call):
        return acme_call
