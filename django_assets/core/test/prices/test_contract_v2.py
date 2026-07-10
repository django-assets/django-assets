"""ADR-0039 v2 vocabulary: quote kinds, capabilities, bounded history shapes.

The fixed vocabulary (PriceKind, Resolution), the discovery answer
(PriceCapabilities + DateRange), and the series shapes (Candle,
OHLCVSeries) are all frozen, Decimal-guarded value objects. OptionQuote
extends PriceQuote with greeks — honest absence is None, never zero.
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.core.prices import (
    Candle,
    DateRange,
    OHLCVSeries,
    OptionQuote,
    PriceCapabilities,
    PriceKind,
    PriceQuote,
    PriceSource,
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


# -- fixed vocabulary ------------------------------------------------------


def test_price_kind_fixed_vocabulary():
    assert PriceKind.REALTIME.value == "realtime"
    assert PriceKind.DELAYED.value == "delayed"
    assert PriceKind.EOD.value == "eod"
    assert len(PriceKind) == 3  # no intraday, no free-form strings


def test_resolution_fixed_vocabulary():
    assert Resolution.DAY.value == "day"
    assert Resolution.WEEK.value == "week"
    assert Resolution.MONTH.value == "month"
    assert len(Resolution) == 3  # intraday deliberately reserved


# -- DateRange -------------------------------------------------------------


def test_date_range_contains_is_inclusive():
    bound = DateRange(datetime.date(2020, 1, 2), datetime.date(2020, 12, 31))
    assert datetime.date(2020, 1, 2) in bound
    assert datetime.date(2020, 12, 31) in bound
    assert datetime.date(2020, 6, 15) in bound
    assert datetime.date(2020, 1, 1) not in bound
    assert datetime.date(2021, 1, 1) not in bound


def test_date_range_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        DateRange(datetime.date(2021, 1, 1), datetime.date(2020, 1, 1))


# -- PriceQuote kind narrowing ---------------------------------------------


def test_price_quote_kind_accepts_enum(usd):
    quote = PriceQuote(price=D("1.23"), currency=usd, as_of=None, source="s", kind=PriceKind.EOD)
    assert quote.kind is PriceKind.EOD


def test_price_quote_kind_normalizes_vocabulary_string(usd):
    quote = PriceQuote(price=D("1.23"), currency=usd, as_of=None, source="s", kind="delayed")
    assert quote.kind is PriceKind.DELAYED


def test_price_quote_rejects_free_form_kind(usd):
    # v1 allowed provider strings like "static"/"last"; v2 does not.
    with pytest.raises(ValueError):
        PriceQuote(price=D("1.23"), currency=usd, as_of=None, source="s", kind="static")


def test_price_quote_still_rejects_floats(usd):
    with pytest.raises(TypeError, match="Decimal"):
        PriceQuote(price=1.23, currency=usd, as_of=None, source="s", kind="eod")  # float-ok


# -- Candle / OHLCVSeries ---------------------------------------------------


def test_candle_holds_decimals_and_session_date():
    candle = Candle(
        session=datetime.date(2026, 7, 6),
        open=D("100.0"),
        high=D("101.5"),
        low=D("99.5"),
        close=D("101.0"),
        volume=D("1200"),
    )
    assert candle.close == D("101.0")
    assert candle.session == datetime.date(2026, 7, 6)


def test_candle_rejects_float_prices():
    with pytest.raises(TypeError, match="Decimal"):
        Candle(
            session=datetime.date(2026, 7, 6),
            open=100.0,  # float-ok
            high=D("101.5"),
            low=D("99.5"),
            close=D("101.0"),
            volume=None,
        )


def test_candle_volume_none_means_no_volume():
    candle = Candle(
        session=datetime.date(2026, 7, 6),
        open=D("1"),
        high=D("1"),
        low=D("1"),
        close=D("1"),
        volume=None,
    )
    assert candle.volume is None


def test_candle_rejects_float_volume():
    with pytest.raises(TypeError, match="Decimal"):
        Candle(
            session=datetime.date(2026, 7, 6),
            open=D("1"),
            high=D("1"),
            low=D("1"),
            close=D("1"),
            volume=100.0,  # float-ok
        )


def test_ohlcv_series_shape(aapl, usd):
    candles = [
        Candle(
            session=datetime.date(2026, 7, 6),
            open=D("1"),
            high=D("2"),
            low=D("1"),
            close=D("2"),
            volume=D("10"),
        )
    ]
    series = OHLCVSeries(
        instrument=aapl,
        currency=usd,
        resolution=Resolution.DAY,
        source="test",
        candles=candles,
    )
    assert series.currency == usd
    assert list(series) == candles
    assert len(series) == 1


# -- PriceCapabilities ------------------------------------------------------


def test_capabilities_shape():
    caps = PriceCapabilities(realtime=False, delayed=True, eod=True, closes=None)
    assert caps.delayed is True
    assert caps.closes is None
    assert caps.ohlcv is None
    assert caps.greeks is False  # default: no greeks surface


def test_capabilities_with_history_and_greeks():
    bound = DateRange(datetime.date(2020, 1, 2), datetime.date(2026, 7, 8))
    caps = PriceCapabilities(
        realtime=True, delayed=True, eod=True, closes=bound, ohlcv=bound, greeks=True
    )
    assert caps.closes == bound
    assert caps.ohlcv == bound
    assert caps.greeks is True


def test_capabilities_closes_without_bars_is_legal():
    # Real case: options have dated EOD closes but no bar archive.
    bound = DateRange(datetime.date(2025, 7, 10), datetime.date(2026, 7, 8))
    caps = PriceCapabilities(realtime=False, delayed=True, eod=True, closes=bound)
    assert caps.ohlcv is None


def test_capabilities_bars_must_lie_within_closes():
    closes = DateRange(datetime.date(2024, 1, 2), datetime.date(2026, 1, 2))
    wider = DateRange(datetime.date(2020, 1, 2), datetime.date(2026, 1, 2))
    with pytest.raises(ValueError):
        PriceCapabilities(realtime=False, delayed=False, eod=True, closes=closes, ohlcv=wider)
    with pytest.raises(ValueError):
        PriceCapabilities(realtime=False, delayed=False, eod=True, closes=None, ohlcv=closes)


# -- OptionQuote ------------------------------------------------------------


def test_option_quote_extends_price_quote_with_greeks(usd):
    quote = OptionQuote(
        price=D("5.20"),
        currency=usd,
        as_of=None,
        source="s",
        kind="delayed",
        iv=D("0.3468"),
        delta=D("0.347"),
        gamma=D("0.015"),
        theta=D("-0.05"),
        vega=D("0.264"),
        underlying_price=D("136.12"),
        open_interest=D("61289"),
        volume=D("977"),
    )
    assert isinstance(quote, PriceQuote)
    assert quote.delta == D("0.347")


def test_option_quote_greeks_default_to_honest_absence(usd):
    quote = OptionQuote(price=D("5.20"), currency=usd, as_of=None, source="s", kind="eod")
    assert quote.iv is None
    assert quote.delta is None
    assert quote.underlying_price is None


def test_option_quote_rejects_float_greeks(usd):
    with pytest.raises(TypeError, match="Decimal"):
        OptionQuote(
            price=D("5.20"),
            currency=usd,
            as_of=None,
            source="s",
            kind="eod",
            delta=0.347,  # float-ok
        )


# -- protocol ---------------------------------------------------------------


def test_static_source_satisfies_v2_protocol():
    assert isinstance(StaticPriceSource({}), PriceSource)


def test_v1_shape_no_longer_satisfies_protocol():
    class V1Only:
        def get_price(self, instrument, *, at=None):
            return None

        def get_prices(self, instruments, *, at=None):
            return {}

    assert not isinstance(V1Only(), PriceSource)
