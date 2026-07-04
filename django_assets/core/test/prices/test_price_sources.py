"""C5: PriceSource protocol + reference implementations — ADR-0034 [D-7].

Core stores no prices and ships no providers. PriceQuote is the wire
shape (Decimal-guarded); None means unpriced, surfaced honestly.
StaticPriceSource backs tests/docs/demos; CachedPriceSource wraps any
source with a TTL.
"""

import datetime
from decimal import Decimal

import pytest
from freezegun import freeze_time

from django_assets.core.models import Instrument
from django_assets.core.prices import CachedPriceSource, PriceQuote, StaticPriceSource

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=2, price_currency=usd
    )


def test_static_source_round_trip(aapl, usd):
    source = StaticPriceSource({aapl: "175.50"})
    quote = source.get_price(aapl)
    assert quote is not None
    assert quote.price == D("175.50")
    assert quote.currency == usd
    assert quote.kind == "static"


def test_static_source_none_for_unknown(aapl, usd):
    assert StaticPriceSource({}).get_price(aapl) is None


def test_get_prices_batch(aapl, usd):
    source = StaticPriceSource({aapl: "175.50"})
    quotes = source.get_prices([aapl, usd])
    assert quotes[aapl].price == D("175.50")
    assert quotes[usd] is None


def test_static_source_rejects_floats(aapl):
    with pytest.raises(TypeError, match="Decimal"):
        StaticPriceSource({aapl: 175.50})  # float-ok


def test_price_quote_rejects_floats(aapl, usd):
    with pytest.raises(TypeError, match="Decimal"):
        PriceQuote(price=1.5, currency=usd, as_of=None, source="s", kind="last")  # float-ok


def test_cached_source_serves_within_ttl_and_refreshes_after(aapl, usd):
    calls = []

    class Counting:
        def get_price(self, instrument, *, at=None):
            calls.append(instrument)
            return PriceQuote(
                price=D("100.00"), currency=usd, as_of=None, source="stub", kind="last"
            )

    cached = CachedPriceSource(Counting(), ttl=60)
    with freeze_time("2026-03-13 12:00:00") as clock:
        cached.get_price(aapl)
        cached.get_price(aapl)
        assert len(calls) == 1  # second hit served from cache
        clock.tick(datetime.timedelta(seconds=61))
        cached.get_price(aapl)
        assert len(calls) == 2  # TTL expired, refetched


def test_cached_source_caches_none_results(aapl):
    calls = []

    class Missing:
        def get_price(self, instrument, *, at=None):
            calls.append(instrument)
            return None

    cached = CachedPriceSource(Missing(), ttl=60)
    with freeze_time("2026-03-13 12:00:00"):
        assert cached.get_price(aapl) is None
        assert cached.get_price(aapl) is None
        assert len(calls) == 1
