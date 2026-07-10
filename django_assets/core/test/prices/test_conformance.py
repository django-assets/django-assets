"""The shipped conformance suite (ADR-0039 §8), run against the shipped
reference implementations. Connector packages import the same suite."""

import io

import pytest

from django_assets.core.models import Instrument
from django_assets.core.prices import CachedPriceSource, CSVPriceSource, StaticPriceSource
from django_assets.core.prices_conformance import PriceSourceConformance
from django_assets.core.test.prices.test_csv_source import CSV_ROWS

pytestmark = pytest.mark.django_db


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def priced(aapl):
    return aapl


@pytest.fixture
def unpriceable(usd):
    return usd


class TestStaticPriceSourceConformance(PriceSourceConformance):
    @pytest.fixture
    def source(self, aapl):
        return StaticPriceSource({aapl: "175.50"})


class TestCSVPriceSourceConformance(PriceSourceConformance):
    @pytest.fixture
    def source(self, aapl):
        return CSVPriceSource({aapl: io.StringIO(CSV_ROWS)})


class TestCachedCSVPriceSourceConformance(PriceSourceConformance):
    """The caching wrapper must be contract-transparent."""

    @pytest.fixture
    def source(self, aapl):
        return CachedPriceSource(CSVPriceSource({aapl: io.StringIO(CSV_ROWS)}), ttl=60)
