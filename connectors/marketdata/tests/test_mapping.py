"""Instrument → vendor symbol mapping (Identifier / OCC synthesis)."""

import datetime
from decimal import Decimal

import pytest
from django_assets_prices_marketdata.mapping import map_instrument

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.options.models import OptionMeta

pytestmark = pytest.mark.django_db


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def eur():
    return Instrument.objects.create(code="EUR", quantity_decimals=2)


def stock(code, currency, ticker=None):
    instrument = Instrument.objects.create(code=code, price_currency=currency)
    if ticker:
        Identifier.objects.create(
            instrument=instrument, type="ticker", value=ticker, is_active=True
        )
    return instrument


def option(underlying, currency, *, strike, expiry=datetime.date(2026, 8, 7), right="C", opra=None):
    instrument = Instrument.objects.create(
        code=f"{underlying.code}-OPT", multiplier=Decimal("100"), price_currency=currency
    )
    OptionMeta.objects.create(
        instrument=instrument,
        underlying=underlying,
        expiry=expiry,
        strike=Decimal(strike),
        right=right,
    )
    if opra:
        Identifier.objects.create(instrument=instrument, type="opra", value=opra, is_active=True)
    return instrument


def test_stock_maps_through_ticker_identifier(usd):
    mapped = map_instrument(stock("Apple Inc", usd, ticker="AAPL"))
    assert mapped is not None
    assert mapped.symbol == "AAPL"
    assert mapped.is_option is False


def test_stock_without_identifier_is_unmappable(usd):
    assert map_instrument(stock("Mystery", usd)) is None


def test_currency_itself_is_unmappable(usd):
    assert map_instrument(usd) is None


def test_non_usd_priced_instrument_is_unmappable(eur):
    assert map_instrument(stock("SAP SE", eur, ticker="SAP")) is None


def test_inactive_ticker_is_ignored(usd):
    instrument = Instrument.objects.create(code="Old", price_currency=usd)
    Identifier.objects.create(instrument=instrument, type="ticker", value="OLD", is_active=False)
    assert map_instrument(instrument) is None


def test_option_synthesizes_occ_symbol(usd):
    underlying = stock("ACME Corp", usd, ticker="ACME")
    mapped = map_instrument(option(underlying, usd, strike="752"))
    assert mapped is not None
    assert mapped.symbol == "ACME260807C00752000"
    assert mapped.is_option is True


def test_option_occ_pads_fractional_strike(usd):
    underlying = stock("ACME Corp", usd, ticker="ACME")
    mapped = map_instrument(option(underlying, usd, strike="7.50", right="P"))
    assert mapped.symbol == "ACME260807P00007500"


def test_option_prefers_explicit_opra_identifier(usd):
    underlying = stock("ACME Corp", usd, ticker="ACME")
    mapped = map_instrument(option(underlying, usd, strike="752", opra="ACME1260807C00752000"))
    assert mapped.symbol == "ACME1260807C00752000"


def test_option_without_underlying_ticker_is_unmappable(usd):
    underlying = stock("No Ticker Corp", usd)
    assert map_instrument(option(underlying, usd, strike="752")) is None


def test_option_with_unencodable_strike_is_unmappable(usd):
    underlying = stock("ACME Corp", usd, ticker="ACME")
    assert map_instrument(option(underlying, usd, strike="7.5005")) is None  # sub-mill precision
