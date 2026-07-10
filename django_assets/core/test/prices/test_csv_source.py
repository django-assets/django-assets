"""CSVPriceSource — the example connector (ADR-0039 §6).

Implements the full v2 contract from per-instrument OHLCV rows: honest
capabilities derived from the data, last close as the quote, exact-session
closes, bounded/clipped series, weekly/monthly aggregated from daily.
"""

import datetime
import io
from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.core.prices import CSVPriceSource, DateRange, PriceKind, Resolution

pytestmark = pytest.mark.django_db

D = Decimal

# Two ISO weeks around a "holiday" gap (2026-01-01 missing), month boundary
# covered by the second fixture below.
CSV_ROWS = """\
session,open,high,low,close,volume
2025-12-29,100.0,102.0,99.0,101.0,1000
2025-12-30,101.0,103.0,100.5,102.5,1100
2025-12-31,102.5,102.5,101.0,101.5,900
2026-01-02,101.5,105.0,101.5,104.0,1500
2026-01-05,104.0,104.5,102.0,103.0,1200
2026-01-06,103.0,106.0,103.0,105.5,1300
"""


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def source(aapl):
    return CSVPriceSource({aapl: io.StringIO(CSV_ROWS)})


def test_capabilities_derived_from_data(source, aapl, usd):
    caps = source.capabilities(aapl)
    assert caps is not None
    assert (caps.realtime, caps.delayed, caps.eod) == (False, False, True)
    bound = DateRange(datetime.date(2025, 12, 29), datetime.date(2026, 1, 6))
    assert caps.closes == bound
    assert caps.ohlcv == bound
    assert source.capabilities(usd) is None


def test_quote_is_last_close(source, aapl, usd):
    quote = source.get_quote(aapl)
    assert quote is not None
    assert quote.price == D("105.5")
    assert quote.kind is PriceKind.EOD
    assert quote.currency == usd


def test_specific_unsupported_kind_is_none(source, aapl):
    assert source.get_quote(aapl, kind=PriceKind.REALTIME) is None
    assert source.get_quote(aapl, kind=PriceKind.DELAYED) is None


def test_close_exact_session(source, aapl):
    quote = source.get_close(aapl, on=datetime.date(2026, 1, 2))
    assert quote is not None
    assert quote.price == D("104.0")
    assert quote.kind is PriceKind.EOD


def test_close_none_for_non_session_and_out_of_bounds(source, aapl):
    assert source.get_close(aapl, on=datetime.date(2026, 1, 1)) is None  # holiday gap
    assert source.get_close(aapl, on=datetime.date(2025, 12, 28)) is None  # before min
    assert source.get_close(aapl, on=datetime.date(2026, 1, 7)) is None  # after max


def test_ohlcv_daily_slice_clipped_to_bounds(source, aapl, usd):
    series = source.get_ohlcv(aapl, start=datetime.date(2025, 1, 1), end=datetime.date(2027, 1, 1))
    assert series is not None
    assert series.resolution is Resolution.DAY
    assert series.currency == usd
    sessions = [c.session for c in series]
    assert sessions[0] == datetime.date(2025, 12, 29)
    assert sessions[-1] == datetime.date(2026, 1, 6)
    assert sessions == sorted(sessions)
    assert len(sessions) == 6  # holidays absent, no gap-filling


def test_ohlcv_subrange(source, aapl):
    series = source.get_ohlcv(
        aapl, start=datetime.date(2025, 12, 30), end=datetime.date(2026, 1, 5)
    )
    assert [c.session for c in series] == [
        datetime.date(2025, 12, 30),
        datetime.date(2025, 12, 31),
        datetime.date(2026, 1, 2),
        datetime.date(2026, 1, 5),
    ]


def test_ohlcv_weekly_aggregation(source, aapl):
    series = source.get_ohlcv(
        aapl,
        start=datetime.date(2025, 12, 29),
        end=datetime.date(2026, 1, 6),
        resolution=Resolution.WEEK,
    )
    # ISO week 2026-W01 spans 2025-12-29..2026-01-02 (4 sessions); W02 has 2.
    assert len(series.candles) == 2
    week1, week2 = series.candles
    assert week1.session == datetime.date(2026, 1, 2)  # last session in the week
    assert week1.open == D("100.0")
    assert week1.high == D("105.0")
    assert week1.low == D("99.0")
    assert week1.close == D("104.0")
    assert week1.volume == D("4500")
    assert week2.session == datetime.date(2026, 1, 6)  # honestly partial
    assert week2.close == D("105.5")


def test_ohlcv_monthly_aggregation(source, aapl):
    series = source.get_ohlcv(
        aapl,
        start=datetime.date(2025, 12, 1),
        end=datetime.date(2026, 1, 31),
        resolution=Resolution.MONTH,
    )
    assert len(series.candles) == 2
    december, january = series.candles
    assert december.session == datetime.date(2025, 12, 31)
    assert december.open == D("100.0")
    assert december.close == D("101.5")
    assert december.volume == D("3000")
    assert january.session == datetime.date(2026, 1, 6)
    assert january.open == D("101.5")
    assert january.close == D("105.5")


def test_ohlcv_none_for_unknown_instrument(source, usd):
    assert (
        source.get_ohlcv(usd, start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 6))
        is None
    )


def test_ohlcv_rejects_inverted_range(source, aapl):
    with pytest.raises(ValueError):
        source.get_ohlcv(aapl, start=datetime.date(2026, 1, 6), end=datetime.date(2026, 1, 1))


def test_ohlcv_empty_when_no_overlap(source, aapl):
    series = source.get_ohlcv(
        aapl, start=datetime.date(2024, 1, 1), end=datetime.date(2024, 12, 31)
    )
    assert series is not None
    assert list(series) == []


def test_csv_volume_optional(aapl, usd):
    rows = "session,open,high,low,close\n2026-01-02,1.0,2.0,0.5,1.5\n"
    source = CSVPriceSource({aapl: io.StringIO(rows)})
    series = source.get_ohlcv(aapl, start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))
    assert series.candles[0].volume is None
    # Aggregates with any missing volume stay honestly None.
    weekly = source.get_ohlcv(
        aapl,
        start=datetime.date(2026, 1, 1),
        end=datetime.date(2026, 1, 31),
        resolution=Resolution.WEEK,
    )
    assert weekly.candles[0].volume is None


def test_batch_quotes(source, aapl, usd):
    quotes = source.get_quotes([aapl, usd])
    assert quotes[aapl].price == D("105.5")
    assert quotes[usd] is None
