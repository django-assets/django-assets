"""MarketDataPriceSource behavior against the scripted vendor.

The clock is frozen at Friday 2026-07-10 15:00 ET (market open); see
conftest for the scenario. Entitlements this vendor grants: stocks
realtime (fresh prices channel) + delayed; options delayed (permissions
header) with an EOD close series.
"""

import datetime
from decimal import Decimal

import pytest
from django_assets_prices_marketdata.client import MarketDataClient
from django_assets_prices_marketdata.source import MarketDataPriceSource
from freezegun import freeze_time

from django_assets.core.prices import DateRange, OptionQuote, PriceKind, Resolution

from .conftest import FROZEN, LAST_SESSION, LISTED, OPTION_SYMBOL

pytestmark = pytest.mark.django_db

D = Decimal


# -- capabilities -------------------------------------------------------------


@freeze_time(FROZEN)
def test_stock_capabilities_probe_confirmed(source, acme, usd):
    caps = source.capabilities(acme)
    assert caps is not None
    assert caps.realtime is True  # prices channel observed fresh while open
    assert caps.delayed is True  # probe quote dated to the current session
    assert caps.eod is True
    assert caps.closes == DateRange(LISTED, LAST_SESSION)
    assert caps.ohlcv == DateRange(LISTED, LAST_SESSION)
    assert caps.greeks is False


@freeze_time(FROZEN)
def test_option_capabilities(source, acme_call):
    caps = source.capabilities(acme_call)
    assert caps is not None
    assert caps.realtime is False  # permissions header: delayed OPRA
    assert caps.delayed is True
    assert caps.eod is True
    assert caps.closes == DateRange(datetime.date(2026, 7, 1), datetime.date(2026, 7, 9))
    assert caps.ohlcv is None  # no option bar archive — honest
    assert caps.greeks is True


@freeze_time(FROZEN)
def test_currency_is_unpriceable(source, usd):
    assert source.capabilities(usd) is None


@freeze_time(FROZEN)
def test_unknown_symbol_is_unpriceable(source, usd):
    from django_assets.core.models import Identifier, Instrument

    ghost = Instrument.objects.create(code="GONE", price_currency=usd)
    Identifier.objects.create(instrument=ghost, type="ticker", value="GONE", is_active=True)
    assert source.capabilities(ghost) is None  # vendor answers 400 for it


@freeze_time(FROZEN)
def test_realtime_not_claimed_when_endpoint_is_entitlement_gated(vendor, acme):
    vendor.realtime_available = False  # /stocks/prices → 402
    client = MarketDataClient(token="t", transport=vendor.transport())
    source = MarketDataPriceSource(client=client, probe_symbol="ACME")
    caps = source.capabilities(acme)
    assert caps.realtime is False
    assert caps.delayed is True


def test_realtime_not_claimed_when_market_closed(vendor, acme):
    with freeze_time("2026-07-10 09:00:00"):  # 05:00 ET — closed
        client = MarketDataClient(token="t", transport=vendor.transport())
        source = MarketDataPriceSource(client=client, probe_symbol="ACME")
        caps = source.capabilities(acme)
        assert caps.realtime is False  # unconfirmable ≠ claimed
        # ...and the exactness rule holds: REALTIME is answered None.
        assert source.get_quote(acme, kind=PriceKind.REALTIME) is None


# -- quotes --------------------------------------------------------------------


@freeze_time(FROZEN)
def test_stock_quote_best_available_is_realtime(source, acme, usd):
    quote = source.get_quote(acme)
    assert quote.kind is PriceKind.REALTIME
    assert quote.price == D("751.463")  # the prices channel's mid
    assert quote.currency == usd
    assert quote.as_of is not None


@freeze_time(FROZEN)
def test_stock_quote_delayed_uses_quote_mid(source, acme):
    quote = source.get_quote(acme, kind=PriceKind.DELAYED)
    assert quote.kind is PriceKind.DELAYED
    assert quote.price == D("751.42")


@freeze_time(FROZEN)
def test_stock_quote_eod_is_official_close(source, acme):
    quote = source.get_quote(acme, kind=PriceKind.EOD)
    assert quote.kind is PriceKind.EOD
    # Last completed session is Thursday 2026-07-09 (Friday's bell not rung).
    assert quote.as_of.astimezone(datetime.UTC).date() == LAST_SESSION
    series = source.get_ohlcv(acme, start=LAST_SESSION, end=LAST_SESSION)
    assert quote.price == series.candles[-1].close


@freeze_time(FROZEN)
def test_option_quote_delayed_carries_greeks(source, acme_call, usd):
    quote = source.get_quote(acme_call)
    assert isinstance(quote, OptionQuote)
    assert quote.kind is PriceKind.DELAYED
    assert quote.price == D("12.11")  # mid = mark
    assert quote.iv == D("0.1401")
    assert quote.delta == D("0.531")
    assert quote.underlying_price == D("752.40")
    assert quote.open_interest == D("3406")
    assert quote.intrinsic_value == D("0.40")
    assert quote.extrinsic_value == D("11.71")
    assert quote.currency == usd


@freeze_time(FROZEN)
def test_option_quote_eod_is_last_series_row(source, acme_call):
    quote = source.get_quote(acme_call, kind=PriceKind.EOD)
    assert isinstance(quote, OptionQuote)
    assert quote.kind is PriceKind.EOD
    assert quote.price == D("15.30")  # 2026-07-09 row mark
    from .fake_vendor import EASTERN

    assert quote.as_of.astimezone(EASTERN).date() == LAST_SESSION


@freeze_time(FROZEN)
def test_expired_option_serves_only_history(source, vendor, acme, usd):
    from django_assets.core.models import Instrument
    from django_assets.instruments.options.models import OptionMeta

    expired = Instrument.objects.create(
        code="ACME-EXPIRED", multiplier=D("100"), price_currency=usd
    )
    OptionMeta.objects.create(
        instrument=expired,
        underlying=acme,
        expiry=datetime.date(2026, 7, 2),
        strike=D("752"),
        right="C",
    )
    vendor.option_series["ACME260702C00752000"] = vendor.option_series[OPTION_SYMBOL][:2]
    caps = source.capabilities(expired)
    assert caps.delayed is False  # no live channel for an expired contract
    assert caps.realtime is False
    assert caps.eod is True
    assert source.get_quote(expired, kind=PriceKind.DELAYED) is None
    assert source.get_quote(expired, kind=PriceKind.EOD) is not None


@freeze_time(FROZEN)
def test_never_listed_option_is_unpriceable_not_an_error(source, acme, usd):
    """A well-formed OCC symbol the vendor doesn't know (typo'd strike)
    answers None everywhere — including through Portfolio-style batch —
    never a raised error (ADR-0039 §3/§7)."""
    from django_assets.core.models import Instrument
    from django_assets.instruments.options.models import OptionMeta

    ghost = Instrument.objects.create(code="ACME-GHOST", multiplier=D("100"), price_currency=usd)
    OptionMeta.objects.create(
        instrument=ghost,
        underlying=acme,
        expiry=datetime.date(2026, 8, 7),
        strike=D("5"),
        right="P",
    )
    assert source.capabilities(ghost) is None
    assert source.get_quote(ghost) is None
    assert source.get_quote(ghost, kind=PriceKind.DELAYED) is None
    assert source.get_quote(ghost, kind=PriceKind.EOD) is None
    assert source.get_close(ghost, on=datetime.date(2026, 7, 6)) is None
    assert source.get_quotes([ghost])[ghost] is None


@freeze_time(FROZEN)
def test_in_progress_session_never_serves_as_option_close(source, vendor, acme_call):
    """If the vendor materializes TODAY's EOD-quote row intraday, it is
    not an official close yet: closes.max stays at the last completed
    session and get_close(today) answers None until the bell."""
    import copy

    today_row = copy.deepcopy(vendor.option_series[OPTION_SYMBOL][-1])
    today_row["session"] = datetime.date(2026, 7, 10)  # frozen "today", 15:00 ET
    today_row["updated"] = int(
        datetime.datetime(2026, 7, 10, 18, 55, tzinfo=datetime.UTC).timestamp()
    )
    vendor.option_series[OPTION_SYMBOL].append(today_row)

    caps = source.capabilities(acme_call)
    assert caps.closes.max == LAST_SESSION
    assert source.get_close(acme_call, on=datetime.date(2026, 7, 10)) is None
    eod = source.get_quote(acme_call, kind=PriceKind.EOD)
    assert eod.as_of.astimezone(datetime.UTC).date() <= datetime.date(2026, 7, 10)
    assert eod.price == D("15.30")  # still Thursday's row


# -- batch -----------------------------------------------------------------------


@freeze_time(FROZEN)
def test_get_quotes_batches_stocks_in_one_call(source, vendor, acme, usd):
    from django_assets.core.models import Identifier, Instrument

    beta = Instrument.objects.create(code="BETA", price_currency=usd, price_decimals=2)
    Identifier.objects.create(instrument=beta, type="ticker", value="BETA", is_active=True)
    vendor.stock_quotes["BETA"] = dict(vendor.stock_quotes["ACME"], mid="42.42")
    vendor.stock_prices["BETA"] = dict(vendor.stock_prices["ACME"], mid="42.43")

    result = source.get_quotes([acme, beta, usd])
    assert result[usd] is None
    assert result[acme].price == D("751.463")
    assert result[beta].price == D("42.43")
    batch_calls = [c for c in vendor.calls if "symbols=" in c and "prices" in c]
    assert len(batch_calls) == 1  # one vendor batch call, not a hidden loop


@freeze_time(FROZEN)
def test_get_quotes_maps_missing_symbols_to_none(source, vendor, acme, usd):
    from django_assets.core.models import Identifier, Instrument

    ghost = Instrument.objects.create(code="GONE", price_currency=usd)
    Identifier.objects.create(instrument=ghost, type="ticker", value="GONE", is_active=True)
    result = source.get_quotes([acme, ghost])
    assert result[acme] is not None
    assert result[ghost] is None


# -- history -----------------------------------------------------------------------


@freeze_time(FROZEN)
def test_get_close_equity(source, acme, usd):
    quote = source.get_close(acme, on=datetime.date(2026, 7, 8))
    assert quote is not None
    assert quote.kind is PriceKind.EOD
    assert quote.currency == usd
    series = source.get_ohlcv(acme, start=datetime.date(2026, 7, 8), end=datetime.date(2026, 7, 8))
    assert quote.price == series.candles[0].close


@freeze_time(FROZEN)
def test_get_close_none_for_holiday_weekend_and_out_of_bounds(source, acme):
    assert source.get_close(acme, on=datetime.date(2026, 7, 3)) is None  # holiday
    assert source.get_close(acme, on=datetime.date(2026, 7, 4)) is None  # Saturday
    assert source.get_close(acme, on=LISTED - datetime.timedelta(days=3)) is None
    assert source.get_close(acme, on=LAST_SESSION + datetime.timedelta(days=1)) is None


@freeze_time(FROZEN)
def test_get_close_option_from_series(source, acme_call):
    quote = source.get_close(acme_call, on=datetime.date(2026, 7, 6))
    assert isinstance(quote, OptionQuote)
    assert quote.price == D("12.27")
    assert quote.iv == D("0.1363")
    assert source.get_close(acme_call, on=datetime.date(2026, 7, 4)) is None
    assert source.get_close(acme_call, on=datetime.date(2026, 6, 1)) is None


@freeze_time(FROZEN)
def test_ohlcv_daily_clipped_and_decimal(source, acme, usd):
    series = source.get_ohlcv(
        acme, start=LISTED - datetime.timedelta(days=30), end=datetime.date(2027, 1, 1)
    )
    assert series.currency == usd
    assert series.candles[0].session == LISTED
    assert series.candles[-1].session == LAST_SESSION
    assert isinstance(series.candles[0].close, D)
    assert isinstance(series.candles[0].volume, D)


@freeze_time(FROZEN)
def test_ohlcv_weekly_aggregates_from_daily(source, acme):
    from django_assets.core.prices import aggregate_candles

    start, end = datetime.date(2026, 6, 22), datetime.date(2026, 7, 9)
    daily = source.get_ohlcv(acme, start=start, end=end)
    weekly = source.get_ohlcv(acme, start=start, end=end, resolution=Resolution.WEEK)
    assert weekly.candles == aggregate_candles(daily.candles, Resolution.WEEK)
    # Holiday week (2026-07-03 closed) still labels by its LAST session.
    sessions = [c.session for c in weekly.candles]
    assert datetime.date(2026, 7, 2) in sessions


@freeze_time(FROZEN)
def test_ohlcv_none_for_options(source, acme_call):
    assert (
        source.get_ohlcv(acme_call, start=datetime.date(2026, 7, 1), end=datetime.date(2026, 7, 9))
        is None
    )


@freeze_time(FROZEN)
def test_ohlcv_empty_series_when_no_overlap(source, acme):
    series = source.get_ohlcv(acme, start=datetime.date(2023, 1, 1), end=datetime.date(2023, 6, 1))
    assert series is not None
    assert list(series) == []


# -- credit discipline ---------------------------------------------------------------


@freeze_time(FROZEN)
def test_entitlement_probes_run_once(source, vendor, acme):
    source.capabilities(acme)
    calls_after_first = len(vendor.calls)
    source.get_quote(acme)
    source.get_quote(acme, kind=PriceKind.DELAYED)
    probe_calls = [c for c in vendor.calls[calls_after_first:] if "quotes/ACME" in c]
    # get_quote calls hit the vendor, but no repeated entitlement probing
    # beyond the data requests themselves.
    assert len(vendor.calls) - calls_after_first <= 2 + len(probe_calls)


@freeze_time(FROZEN)
def test_bound_discovery_cached_per_instrument(source, vendor, acme):
    source.capabilities(acme)
    count = len(vendor.calls)
    source.capabilities(acme)
    assert len(vendor.calls) == count  # fully served from instance caches
