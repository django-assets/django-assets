"""C5: PriceSource reference implementations — ADR-0034 as amended by ADR-0039.

Core stores no prices and ships no providers. StaticPriceSource is the
eod-only test/docs workhorse; CachedPriceSource wraps the full v2
contract with kind-aware keys and split quote/history TTLs. None means
unpriced, surfaced honestly.
"""

import datetime
from decimal import Decimal

import pytest
from freezegun import freeze_time

from django_assets.core.models import Instrument
from django_assets.core.prices import (
    CachedPriceSource,
    PriceKind,
    PriceQuote,
    Resolution,
    StaticPriceSource,
)

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


# -- StaticPriceSource (eod-only per ADR-0039 §6) ---------------------------


def test_static_source_quotes_eod(aapl, usd):
    source = StaticPriceSource({aapl: "175.50"})
    quote = source.get_quote(aapl)
    assert quote is not None
    assert quote.price == D("175.50")
    assert quote.currency == usd
    assert quote.kind is PriceKind.EOD


def test_static_source_capabilities_are_honest(aapl, usd):
    source = StaticPriceSource({aapl: "175.50"})
    caps = source.capabilities(aapl)
    assert caps is not None
    assert (caps.realtime, caps.delayed, caps.eod) == (False, False, True)
    assert caps.closes is None
    assert caps.ohlcv is None
    assert caps.greeks is False
    assert source.capabilities(usd) is None  # unknown instrument: unpriceable


def test_static_source_specific_kind_is_exact(aapl):
    source = StaticPriceSource({aapl: "175.50"})
    assert source.get_quote(aapl, kind=PriceKind.REALTIME) is None
    assert source.get_quote(aapl, kind=PriceKind.DELAYED) is None
    quote = source.get_quote(aapl, kind=PriceKind.EOD)
    assert quote is not None and quote.kind is PriceKind.EOD


def test_static_source_none_for_unknown(aapl):
    assert StaticPriceSource({}).get_quote(aapl) is None


def test_static_source_no_history(aapl):
    source = StaticPriceSource({aapl: "175.50"})
    assert source.get_close(aapl, on=datetime.date(2026, 7, 6)) is None
    assert (
        source.get_ohlcv(aapl, start=datetime.date(2026, 1, 1), end=datetime.date(2026, 7, 6))
        is None
    )


def test_get_quotes_batch(aapl, usd):
    source = StaticPriceSource({aapl: "175.50"})
    quotes = source.get_quotes([aapl, usd])
    assert quotes[aapl].price == D("175.50")
    assert quotes[usd] is None


def test_static_source_rejects_floats(aapl):
    with pytest.raises(TypeError, match="Decimal"):
        StaticPriceSource({aapl: 175.50})  # float-ok


# -- CachedPriceSource (v2 wrapper) ------------------------------------------


class Counting:
    """Minimal v2 source that counts calls; realtime+eod, no history."""

    def __init__(self, usd):
        self.usd = usd
        self.calls = []

    def capabilities(self, instrument):
        self.calls.append(("capabilities", instrument))
        from django_assets.core.prices import PriceCapabilities

        return PriceCapabilities(realtime=True, delayed=False, eod=True, closes=None)

    def get_quote(self, instrument, *, kind=None):
        self.calls.append(("get_quote", instrument, kind))
        resolved = PriceKind.REALTIME if kind is None else kind
        if resolved is PriceKind.DELAYED:
            return None
        return PriceQuote(
            price=D("100.00"), currency=self.usd, as_of=None, source="stub", kind=resolved
        )

    def get_quotes(self, instruments, *, kind=None):
        return {inst: self.get_quote(inst, kind=kind) for inst in instruments}

    def get_close(self, instrument, on):
        self.calls.append(("get_close", instrument, on))
        return None

    def get_ohlcv(self, instrument, *, start, end, resolution=Resolution.DAY):
        self.calls.append(("get_ohlcv", instrument, start, end, resolution))
        return None


def test_cached_source_serves_within_ttl_and_refreshes_after(aapl, usd):
    inner = Counting(usd)
    cached = CachedPriceSource(inner, ttl=60)
    with freeze_time("2026-03-13 12:00:00") as clock:
        cached.get_quote(aapl)
        cached.get_quote(aapl)
        assert len([c for c in inner.calls if c[0] == "get_quote"]) == 1
        clock.tick(datetime.timedelta(seconds=61))
        cached.get_quote(aapl)
        assert len([c for c in inner.calls if c[0] == "get_quote"]) == 2


def test_cached_source_keys_are_kind_aware(aapl, usd):
    inner = Counting(usd)
    cached = CachedPriceSource(inner, ttl=60)
    with freeze_time("2026-03-13 12:00:00"):
        best = cached.get_quote(aapl)  # kind=None → realtime
        exact = cached.get_quote(aapl, kind=PriceKind.EOD)
        assert best.kind is PriceKind.REALTIME
        assert exact.kind is PriceKind.EOD
        assert len([c for c in inner.calls if c[0] == "get_quote"]) == 2  # distinct keys


def test_cached_source_caches_none_results(aapl, usd):
    inner = Counting(usd)
    cached = CachedPriceSource(inner, ttl=60)
    with freeze_time("2026-03-13 12:00:00"):
        assert cached.get_quote(aapl, kind=PriceKind.DELAYED) is None
        assert cached.get_quote(aapl, kind=PriceKind.DELAYED) is None
        assert len([c for c in inner.calls if c[0] == "get_quote"]) == 1


def test_cached_source_history_ttl_governs_closes_and_capabilities(aapl, usd):
    inner = Counting(usd)
    cached = CachedPriceSource(inner, ttl=1, history_ttl=3600)
    with freeze_time("2026-03-13 12:00:00") as clock:
        cached.get_close(aapl, on=datetime.date(2026, 3, 12))
        cached.capabilities(aapl)
        clock.tick(datetime.timedelta(seconds=120))  # quote ttl long expired
        cached.get_close(aapl, on=datetime.date(2026, 3, 12))
        cached.capabilities(aapl)
        assert len([c for c in inner.calls if c[0] == "get_close"]) == 1
        assert len([c for c in inner.calls if c[0] == "capabilities"]) == 1


def test_cached_source_get_quotes_batches_only_misses(aapl, usd):
    inner = Counting(usd)
    cached = CachedPriceSource(inner, ttl=60)
    with freeze_time("2026-03-13 12:00:00"):
        cached.get_quote(aapl)
        result = cached.get_quotes([aapl, usd])
        assert result[aapl].price == D("100.00")
        # aapl came from cache; only usd hit the inner source
        quote_calls = [c for c in inner.calls if c[0] == "get_quote"]
        assert [c[1] for c in quote_calls] == [aapl, usd]
