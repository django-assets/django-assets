"""C5: Portfolio.at and Portfolio.value — spec §6/§7, ADR-0016/0034/0039."""

import datetime
import io
from decimal import Decimal

import pytest

from django_assets.core.builder import TransactionBuilder
from django_assets.core.measure import Measure
from django_assets.core.prices import CSVPriceSource, PriceQuote, StaticPriceSource
from django_assets.core.queries import Portfolio

from .conftest import BTC_BUY_TS, BTC_SELL_TS, SELL_TS

pytestmark = pytest.mark.ledger

D = Decimal
UTC = datetime.UTC


def test_at_returns_nonzero_positions_keyed_by_instrument(history, aapl, usd):
    assert Portfolio.at(history["holdings"]) == {aapl: D("60")}
    assert Portfolio.at(history["cash"]) == {usd: D("6100.00")}


def test_zero_positions_excluded(history, btc):
    """BTC was bought and fully sold — it must not appear at zero."""
    assert btc not in Portfolio.at(history["holdings"])


def test_at_historical_snapshot(history, aapl, btc):
    between = BTC_BUY_TS + datetime.timedelta(hours=1)
    snapshot = Portfolio.at(history["holdings"], as_of=between)
    assert snapshot == {aapl: D("60"), btc: D("0.50000000")}


def test_at_is_one_query(history, django_assert_num_queries):
    with django_assert_num_queries(1):
        Portfolio.at(history["holdings"])


def test_same_timestamp_transactions_both_included(history, usd, aapl):
    """Deterministic boundary: two transactions sharing a timestamp are
    both visible at as_of == that timestamp ((timestamp, id) tiebreak)."""
    ts = BTC_SELL_TS + datetime.timedelta(days=1)
    for _ in range(2):
        with TransactionBuilder(account=history["cash"], timestamp=ts) as b:
            b.add_leg(account=history["holdings"], instrument=aapl, amount="10")
            b.add_leg(account=history["external"], instrument=aapl, amount="-10")
    assert Portfolio.at(history["holdings"], as_of=ts)[aapl] == D("80")


def test_value_prices_positions_per_currency(history, aapl, usd):
    result = Portfolio.value(history["holdings"], StaticPriceSource({aapl: "180.00"}))
    assert result.totals == {usd: Measure(D("10800.00"), usd)}
    assert result.unpriced == []


def test_value_cash_is_self_valued(history, usd):
    """A currency position needs no quote: it IS its own value (ADR-0013)."""
    result = Portfolio.value(history["cash"], StaticPriceSource({}))
    assert result.totals == {usd: Measure(D("6100.00"), usd)}
    assert result.unpriced == []


def test_value_unpriced_surfaced_honestly(history, aapl, btc):
    """None from the source = unpriced, listed explicitly, never guessed."""
    between = BTC_BUY_TS + datetime.timedelta(hours=1)  # Fri 2026-03-13 11:00
    # Dated valuations mark at the official close (ADR-0039): AAPL has an
    # archive covering the date; BTC has none and surfaces as unpriced.
    aapl_history = CSVPriceSource(
        {aapl: io.StringIO("session,open,high,low,close\n2026-03-13,179,181,178,180.00\n")}
    )
    result = Portfolio.value(history["holdings"], aapl_history, as_of=between)
    assert result.totals == {aapl.price_currency: Measure(D("10800.00"), aapl.price_currency)}
    assert result.unpriced == [btc]
    assert btc not in result.totals


def test_value_dated_without_archive_is_unpriced(history, aapl, btc):
    """StaticPriceSource has no archive: a dated valuation cannot mark
    anything — honest None, never 'the current price, backdated'."""
    between = BTC_BUY_TS + datetime.timedelta(hours=1)
    result = Portfolio.value(
        history["holdings"], StaticPriceSource({aapl: "180.00"}), as_of=between
    )
    assert set(result.unpriced) == {aapl, btc}
    assert result.totals == {}


def test_value_rejects_currency_mismatch(history, aapl, usd):
    """quote.currency must equal instrument.price_currency — no implicit FX."""

    class EurQuoting:
        def __init__(self):
            self.eur = None

        def get_quote(self, instrument, *, kind=None):
            from django_assets.core.models import Instrument

            self.eur = self.eur or Instrument.objects.create(code="EUR", quantity_decimals=2)
            return PriceQuote(
                price=D("160.00"), currency=self.eur, as_of=None, source="stub", kind="eod"
            )

        def get_quotes(self, instruments, *, kind=None):
            return {inst: self.get_quote(inst, kind=kind) for inst in instruments}

    with pytest.raises(ValueError, match="price_currency"):
        Portfolio.value(history["holdings"], EurQuoting())


def test_value_guards_against_float_quotes(history, aapl):
    """PADR-0006 Rule 3: a source built on floats fails loudly."""

    class FloatSource:
        def get_quote(self, instrument, *, kind=None):
            return PriceQuote(
                price=180.0,  # float-ok
                currency=instrument.price_currency,
                as_of=None,
                source="stub",
                kind="eod",
            )

        def get_quotes(self, instruments, *, kind=None):
            return {inst: self.get_quote(inst, kind=kind) for inst in instruments}

    with pytest.raises(TypeError, match="Decimal"):
        Portfolio.value(history["holdings"], FloatSource())


def test_value_applies_multiplier(history, aapl, usd):
    """An option position values at qty × price × multiplier."""
    from django_assets.core.models import Instrument

    option = Instrument.objects.create(
        code="SPY260618C600",
        quantity_decimals=0,
        price_decimals=2,
        multiplier=D("100"),
        price_currency=usd,
    )
    ts = SELL_TS + datetime.timedelta(days=30)
    with TransactionBuilder(account=history["cash"], timestamp=ts) as b:
        b.add_leg(account=history["holdings"], instrument=option, amount="3")
        b.add_leg(account=history["external"], instrument=option, amount="-3")
    # Only the option is quoted: 3 × 2.50 × 100 = 750.00; AAPL is unpriced.
    result = Portfolio.value(history["holdings"], StaticPriceSource({option: "2.50"}))
    assert result.totals == {usd: Measure(D("750.00"), usd)}
    assert result.unpriced == [aapl]
