"""Reusable PriceSource conformance suite (ADR-0039 §8).

The library's semantics, executable: capabilities honesty, the None
ladder, Decimal purity, bounds clipping, weekly/monthly aggregation,
kind badging. Run in-repo against the reference implementations and
importable by connector packages so every provider is held to the same
bar:

    from django_assets.core.prices_conformance import PriceSourceConformance

    class TestMyConnectorConformance(PriceSourceConformance):
        @pytest.fixture
        def source(self):
            return MyConnector(...)

        @pytest.fixture
        def priced(self):
            return <an Instrument the source can price>

        @pytest.fixture
        def unpriceable(self):
            return <an Instrument it honestly cannot>

Requires pytest (a test-time dependency; the module is never imported by
production code paths).
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.core.prices import (
    Candle,
    OptionQuote,
    PriceCapabilities,
    PriceKind,
    PriceQuote,
    PriceSource,
    Resolution,
    aggregate_candles,
)

# Live sources answer from a moving market; conformance never asserts two
# calls return the same *price*, only the same *shape and honesty*.
_AGGREGATION_WINDOW_DAYS = 90


def _assert_decimal_pure(quote: PriceQuote) -> None:
    assert isinstance(quote.price, Decimal), f"price is {type(quote.price).__name__}, not Decimal"
    if isinstance(quote, OptionQuote):
        for name in (
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "underlying_price",
            "open_interest",
            "volume",
        ):
            value = getattr(quote, name)
            assert value is None or isinstance(value, Decimal), (
                f"{name} is {type(value).__name__}, not Decimal|None"
            )


class PriceSourceConformance:
    """Subclass in a test module and provide `source`, `priced`, and
    `unpriceable` fixtures. Every test is a contract clause."""

    # -- capability discovery ------------------------------------------------

    def test_protocol_shape(self, source: PriceSource) -> None:
        assert isinstance(source, PriceSource)

    def test_capabilities_for_priced_instrument(
        self, source: PriceSource, priced: Instrument
    ) -> None:
        caps = source.capabilities(priced)
        assert isinstance(caps, PriceCapabilities)
        assert caps.realtime or caps.delayed or caps.eod or caps.closes is not None, (
            "a priced instrument must have at least one capability"
        )

    def test_capabilities_none_for_unpriceable(
        self, source: PriceSource, unpriceable: Instrument
    ) -> None:
        assert source.capabilities(unpriceable) is None

    def test_unpriceable_is_none_everywhere(
        self, source: PriceSource, unpriceable: Instrument
    ) -> None:
        assert source.get_quote(unpriceable) is None
        for kind in PriceKind:
            assert source.get_quote(unpriceable, kind=kind) is None
        assert source.get_close(unpriceable, on=datetime.date(2020, 6, 1)) is None
        assert (
            source.get_ohlcv(
                unpriceable, start=datetime.date(2020, 1, 1), end=datetime.date(2020, 12, 31)
            )
            is None
        )

    # -- quote kinds ----------------------------------------------------------

    def test_specific_kind_is_exact(self, source: PriceSource, priced: Instrument) -> None:
        caps = source.capabilities(priced)
        assert caps is not None
        for kind, enabled in (
            (PriceKind.REALTIME, caps.realtime),
            (PriceKind.DELAYED, caps.delayed),
            (PriceKind.EOD, caps.eod),
        ):
            quote = source.get_quote(priced, kind=kind)
            if enabled:
                assert quote is not None, f"capabilities promise {kind} but get_quote is None"
                assert quote.kind is kind, f"asked for {kind}, quote badged {quote.kind}"
            else:
                assert quote is None, f"capabilities deny {kind} but get_quote returned a quote"

    def test_default_kind_is_best_available(self, source: PriceSource, priced: Instrument) -> None:
        caps = source.capabilities(priced)
        assert caps is not None
        expected = next(
            (
                kind
                for kind, enabled in (
                    (PriceKind.REALTIME, caps.realtime),
                    (PriceKind.DELAYED, caps.delayed),
                    (PriceKind.EOD, caps.eod),
                )
                if enabled
            ),
            None,
        )
        quote = source.get_quote(priced)
        if expected is None:
            assert quote is None
        else:
            assert quote is not None
            assert quote.kind is expected, (
                f"best-available should badge {expected}, got {quote.kind}"
            )

    def test_quote_currency_and_purity(self, source: PriceSource, priced: Instrument) -> None:
        quote = source.get_quote(priced)
        if quote is None:
            pytest.skip("source has no current-quote capability for the priced instrument")
        assert quote.currency == priced.price_currency, "quote currency must match the instrument"
        assert quote.source, "quotes carry a provider label"
        _assert_decimal_pure(quote)

    def test_batch_agrees_with_single(
        self, source: PriceSource, priced: Instrument, unpriceable: Instrument
    ) -> None:
        result = source.get_quotes([priced, unpriceable])
        assert set(result.keys()) == {priced, unpriceable}
        assert result[unpriceable] is None
        single = source.get_quote(priced)
        batched = result[priced]
        if single is None:
            assert batched is None
        else:
            assert batched is not None
            assert batched.kind is single.kind
            _assert_decimal_pure(batched)

    # -- history ---------------------------------------------------------------

    def test_history_honesty(self, source: PriceSource, priced: Instrument) -> None:
        caps = source.capabilities(priced)
        assert caps is not None
        if caps.closes is None:
            assert source.get_close(priced, on=datetime.date(2020, 6, 1)) is None
        else:
            # Out-of-bounds closes: None, never interpolated.
            assert source.get_close(priced, on=caps.closes.min - datetime.timedelta(days=1)) is None
            assert source.get_close(priced, on=caps.closes.max + datetime.timedelta(days=1)) is None
        if caps.ohlcv is None:
            assert (
                source.get_ohlcv(
                    priced, start=datetime.date(2020, 1, 1), end=datetime.date(2020, 12, 31)
                )
                is None
            )
            return
        bound = caps.ohlcv
        # A series wider than the bound comes back clipped to it.
        series = source.get_ohlcv(
            priced,
            start=bound.min - datetime.timedelta(days=30),
            end=bound.max + datetime.timedelta(days=30),
        )
        assert series is not None
        assert series.resolution is Resolution.DAY
        assert series.currency == priced.price_currency
        sessions = [candle.session for candle in series]
        assert sessions == sorted(sessions), "candles ascend by session"
        assert len(set(sessions)) == len(sessions), "one candle per session"
        assert all(session in bound for session in sessions), "series clipped to the bound"
        for candle in series:
            assert isinstance(candle, Candle)
            for name in ("open", "high", "low", "close"):
                assert isinstance(getattr(candle, name), Decimal)
            assert candle.volume is None or isinstance(candle.volume, Decimal)

    def test_close_matches_daily_candle(self, source: PriceSource, priced: Instrument) -> None:
        caps = source.capabilities(priced)
        assert caps is not None
        if caps.ohlcv is None:
            pytest.skip("no ohlcv capability")
        bound = caps.ohlcv
        start = max(bound.min, bound.max - datetime.timedelta(days=_AGGREGATION_WINDOW_DAYS))
        series = source.get_ohlcv(priced, start=start, end=bound.max)
        assert series is not None
        if not series.candles:
            pytest.skip("no candles in the probe window")
        last = series.candles[-1]
        close = source.get_close(priced, on=last.session)
        assert close is not None, "a candle session must answer get_close"
        assert close.price == last.close, "get_close and get_ohlcv may never disagree"
        assert close.kind is PriceKind.EOD

    def test_weekly_and_monthly_aggregate_from_daily(
        self, source: PriceSource, priced: Instrument
    ) -> None:
        caps = source.capabilities(priced)
        assert caps is not None
        if caps.ohlcv is None:
            pytest.skip("no ohlcv capability")
        bound = caps.ohlcv
        start = max(bound.min, bound.max - datetime.timedelta(days=_AGGREGATION_WINDOW_DAYS))
        daily = source.get_ohlcv(priced, start=start, end=bound.max)
        assert daily is not None
        for resolution in (Resolution.WEEK, Resolution.MONTH):
            series = source.get_ohlcv(priced, start=start, end=bound.max, resolution=resolution)
            assert series is not None
            assert series.resolution is resolution
            expected = aggregate_candles(daily.candles, resolution)
            assert series.candles == expected, (
                f"{resolution} bars must be the ADR-0039 §5 aggregation of the daily sessions"
            )
