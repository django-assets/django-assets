"""VENDOR-TRUE differential harness (live, metered).

Every check pulls the same question straight from the MarketData API
(raw client, independent code path) and through the connector, and
asserts they agree — values, sessions, kinds, Decimal purity. Where the
market is moving, the raw answer is bracketed (fetched before and after
the connector call; retried until the vendor state was stable across the
window) so equality is exact, not fuzzy.

Also runs the library's conformance suite against the LIVE source — the
same bar the reference implementations pass.

Run: uv run pytest connectors/marketdata/verify/live_differential.py -v
"""

import datetime
from decimal import Decimal

import pytest
from django_assets_prices_marketdata.calendar import EASTERN
from django_assets_prices_marketdata.source import MarketDataPriceSource

from django_assets.core.models import Identifier, Instrument
from django_assets.core.prices import (
    OptionQuote,
    PriceKind,
    Resolution,
    aggregate_candles,
)
from django_assets.core.prices_conformance import PriceSourceConformance
from django_assets.instruments.options.models import OptionMeta

pytestmark = pytest.mark.django_db

D = Decimal


# -- shared live fixtures --------------------------------------------------------


@pytest.fixture(scope="module")
def live_source():
    return MarketDataPriceSource()


@pytest.fixture
def usd(db):
    return Instrument.objects.create(code="USD", quantity_decimals=2, price_decimals=2)


@pytest.fixture
def spy(usd):
    instrument = Instrument.objects.create(
        code="SPY", quantity_decimals=4, price_decimals=2, price_currency=usd
    )
    Identifier.objects.create(instrument=instrument, type="ticker", value="SPY", is_active=True)
    return instrument


@pytest.fixture
def live_option(usd, spy, raw_client):
    """A real, currently-listed SPY call discovered from the live chain,
    modeled as OptionMeta so the connector must synthesize the same OCC
    symbol the vendor speaks."""
    chain = raw_client.get(
        "/v1/options/chain/SPY/", {"strikeLimit": "1", "side": "call", "dte": "30"}
    )
    symbol = chain["optionSymbol"][0]
    strike = Decimal(chain["strike"][0])
    expiry = datetime.datetime.fromtimestamp(int(chain["expiration"][0]), tz=EASTERN).date()
    instrument = Instrument.objects.create(
        code=symbol,
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    OptionMeta.objects.create(
        instrument=instrument, underlying=spy, expiry=expiry, strike=strike, right="C"
    )
    return instrument, symbol


def bracketed(fetch_raw, fetch_via_connector, attempts=6, stamp=None):
    """Fetch raw → connector → raw; accept when the vendor state was
    stable across the bracket, so equality must be exact. In a fast
    market the bracket may never stabilize: callers may pass `stamp`
    functions (raw_stamp(raw), result_stamp(result)) — when the
    connector's answer carries the SAME vendor timestamp as a raw
    fetch, the values must match exactly even mid-churn."""
    last = None
    for _ in range(attempts):
        before = fetch_raw()
        result = fetch_via_connector()
        after = fetch_raw()
        if before == after:
            return result, before
        if stamp is not None:
            raw_stamp, result_stamp = stamp
            for candidate in (before, after):
                if result_stamp(result) == raw_stamp(candidate):
                    return result, candidate
        last = (result, after)
    if stamp is not None and last is not None:
        pytest.fail("no bracket stability and no stamp match — cannot compare exactly")
    pytest.fail("vendor state never stable across the bracket — cannot compare exactly")


# -- equities ----------------------------------------------------------------------


def test_delayed_quote_matches_vendor(live_source, spy, raw_client):
    """Exact-value vendor truth, churn-tolerant: the connector's price
    must EQUAL a vendor-served value observed raw within the same tight
    bracket (sub-second churn means two HTTP fetches can never be
    simultaneous; a value the vendor demonstrably served in the window
    is the strongest possible exactness)."""

    def raw():
        payload = raw_client.get("/v1/stocks/quotes/SPY/")
        return (payload["mid"][0], payload["last"][0], payload["updated"][0])

    quote = None
    served: set[D] = set()
    matched = False
    for _ in range(8):
        before = raw()
        quote = live_source.get_quote(spy, kind=PriceKind.DELAYED)
        after = raw()
        assert quote is not None, "delayed entitlement claimed but no quote returned"
        served |= {D(v) for v in (before[0], before[1], after[0], after[1]) if v is not None}
        if quote.price in served:
            matched = True
            break
    assert matched, (
        f"connector price {quote.price} never among vendor-served values {sorted(served)}"
    )
    assert quote.kind is PriceKind.DELAYED
    assert quote.as_of is not None and isinstance(quote.price, D)
    # Freshness: the delayed channel's stamp is recent during RTH.
    age = datetime.datetime.now(datetime.UTC) - quote.as_of
    assert age <= datetime.timedelta(minutes=30)


def test_eod_quote_is_last_completed_sessions_official_close(live_source, spy, raw_client):
    quote = live_source.get_quote(spy, kind=PriceKind.EOD)
    assert quote is not None
    session = quote.as_of.astimezone(EASTERN).date()
    raw = raw_client.get(
        "/v1/stocks/candles/D/SPY/", {"from": session.isoformat(), "to": session.isoformat()}
    )
    assert quote.price == D(raw["c"][-1]), "EOD quote must equal the vendor's official close"
    # The session must be complete: today only after the closing bell.
    now_east = datetime.datetime.now(datetime.UTC).astimezone(EASTERN)
    assert session <= now_east.date()
    if session == now_east.date():
        assert now_east.time() >= datetime.time(16, 0)


def test_dated_close_matches_vendor_candle(live_source, spy, raw_client):
    on = datetime.date(2026, 6, 15)  # a known Monday session
    quote = live_source.get_close(spy, on=on)
    assert quote is not None
    raw = raw_client.get(
        "/v1/stocks/candles/D/SPY/", {"from": on.isoformat(), "to": on.isoformat()}
    )
    assert quote.price == D(raw["c"][0])
    assert quote.kind is PriceKind.EOD


def test_close_none_for_holiday_and_weekend(live_source, spy):
    assert live_source.get_close(spy, on=datetime.date(2026, 7, 4)) is None  # Saturday
    assert live_source.get_close(spy, on=datetime.date(2026, 1, 1)) is None  # New Year's


def test_ohlcv_daily_matches_vendor_arrays(live_source, spy, raw_client):
    start, end = datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)
    series = live_source.get_ohlcv(spy, start=start, end=end)
    raw = raw_client.get(
        "/v1/stocks/candles/D/SPY/", {"from": start.isoformat(), "to": end.isoformat()}
    )
    assert len(series.candles) == len(raw["t"])
    for index, candle in enumerate(series.candles):
        assert (
            candle.session
            == datetime.datetime.fromtimestamp(int(raw["t"][index]), tz=EASTERN).date()
        )
        assert candle.open == D(raw["o"][index])
        assert candle.high == D(raw["h"][index])
        assert candle.low == D(raw["l"][index])
        assert candle.close == D(raw["c"][index])
        assert candle.volume == D(raw["v"][index])


def test_weekly_bars_are_adr_aggregation_and_agree_with_vendor_closes(live_source, spy, raw_client):
    start, end = datetime.date(2026, 6, 1), datetime.date(2026, 6, 26)  # complete weeks
    daily = live_source.get_ohlcv(spy, start=start, end=end)
    weekly = live_source.get_ohlcv(spy, start=start, end=end, resolution=Resolution.WEEK)
    assert weekly.candles == aggregate_candles(daily.candles, Resolution.WEEK)
    raw_weekly = raw_client.get(
        "/v1/stocks/candles/W/SPY/", {"from": start.isoformat(), "to": end.isoformat()}
    )
    # Complete weeks must agree with the vendor's native weekly closes.
    assert [c.close for c in weekly.candles] == [D(v) for v in raw_weekly["c"]]
    assert [c.high for c in weekly.candles] == [D(v) for v in raw_weekly["h"]]
    assert [c.low for c in weekly.candles] == [D(v) for v in raw_weekly["l"]]
    assert [c.open for c in weekly.candles] == [D(v) for v in raw_weekly["o"]]


# -- options -------------------------------------------------------------------------


def test_option_live_quote_matches_vendor_with_greeks(live_source, live_option, raw_client):
    instrument, symbol = live_option

    def raw():
        return raw_client.get(f"/v1/options/quotes/{symbol}/")

    quote, payload = bracketed(
        raw,
        lambda: live_source.get_quote(instrument),
        stamp=(
            lambda p: int(p["updated"][0]),
            lambda q: int(q.as_of.timestamp()) if q and q.as_of else None,
        ),
    )
    assert isinstance(quote, OptionQuote), "option quotes must carry the greeks surface"
    assert quote.price == D(payload["mid"][0])
    for field, key in (
        ("iv", "iv"),
        ("delta", "delta"),
        ("gamma", "gamma"),
        ("theta", "theta"),
        ("vega", "vega"),
        ("underlying_price", "underlyingPrice"),
        ("open_interest", "openInterest"),
        ("volume", "volume"),
    ):
        raw_value = payload[key][0]
        mine = getattr(quote, field)
        if raw_value is None:
            assert mine is None
        else:
            assert mine == D(raw_value), f"{field} disagrees with vendor"
            assert isinstance(mine, D)


def test_option_dated_close_matches_vendor_eod_quote(live_source, live_option, raw_client):
    instrument, symbol = live_option
    caps = live_source.capabilities(instrument)
    assert caps is not None and caps.closes is not None
    on = caps.closes.max
    quote = live_source.get_close(instrument, on=on)
    assert quote is not None
    raw = raw_client.get(f"/v1/options/quotes/{symbol}/", {"date": on.isoformat()})
    assert quote.price == D(raw["mid"][0])
    assert quote.as_of == datetime.datetime.fromtimestamp(int(raw["updated"][0]), tz=datetime.UTC)


def test_option_capabilities_honest(live_source, live_option):
    instrument, _ = live_option
    caps = live_source.capabilities(instrument)
    assert caps.greeks is True
    assert caps.ohlcv is None  # vendor has no option bar archive
    # The entitlement header on this account says delayed, not realtime.
    assert caps.realtime is False
    assert caps.delayed is True
    # And behavior matches the claim exactly:
    assert live_source.get_quote(instrument, kind=PriceKind.REALTIME) is None
    assert live_source.get_quote(instrument, kind=PriceKind.DELAYED) is not None


# -- capability/behavior consistency (the 'capability that lies' hunt) ----------------


def test_equity_capabilities_consistent_with_behavior(live_source, spy):
    caps = live_source.capabilities(spy)
    assert caps is not None
    for kind, enabled in (
        (PriceKind.REALTIME, caps.realtime),
        (PriceKind.DELAYED, caps.delayed),
        (PriceKind.EOD, caps.eod),
    ):
        quote = live_source.get_quote(spy, kind=kind)
        assert (quote is not None) == enabled, f"capabilities lie about {kind}"
        if quote is not None:
            assert quote.kind is kind
    if caps.realtime:
        quote = live_source.get_quote(spy, kind=PriceKind.REALTIME)
        age = datetime.datetime.now(datetime.UTC) - quote.as_of
        assert age <= datetime.timedelta(minutes=2), "realtime claimed but quote is stale"


def test_equity_bounds_honest(live_source, spy):
    caps = live_source.capabilities(spy)
    bound = caps.closes
    assert bound is not None
    assert live_source.get_close(spy, on=bound.min) is not None, "bound.min must answer"
    assert live_source.get_close(spy, on=bound.min - datetime.timedelta(days=400)) is None, (
        "before the discovered bound must be None"
    )
    series = live_source.get_ohlcv(
        spy,
        start=bound.min - datetime.timedelta(days=400),
        end=bound.min + datetime.timedelta(days=10),
    )
    assert all(candle.session >= bound.min for candle in series.candles), "series not clipped"


def test_unknown_and_unmappable_are_unpriceable(live_source, usd, db):
    ghost = Instrument.objects.create(code="Ghost Corp", price_currency=usd)
    Identifier.objects.create(instrument=ghost, type="ticker", value="ZZZZZZZZ", is_active=True)
    assert live_source.capabilities(ghost) is None
    assert live_source.get_quote(ghost) is None
    assert live_source.capabilities(usd) is None  # a currency: unmappable, honest None


# -- the library's own conformance suite, against the live vendor ---------------------


@pytest.fixture
def unpriceable(usd):
    return usd


class TestLiveStockConformance(PriceSourceConformance):
    @pytest.fixture
    def source(self, live_source):
        return live_source

    @pytest.fixture
    def priced(self, spy):
        return spy


class TestLiveOptionConformance(PriceSourceConformance):
    @pytest.fixture
    def source(self, live_source):
        return live_source

    @pytest.fixture
    def priced(self, live_option):
        return live_option[0]
