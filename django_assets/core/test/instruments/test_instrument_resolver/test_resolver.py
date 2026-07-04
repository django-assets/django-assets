"""C4: instrument resolver — spec §5, ADR-0009 contract, ADR-0018 shapes.

resolve() is one-or-raise for backend code; search() returns all candidates
for UI code. Default normalization strips whitespace only and preserves
case (preferred-share tickers like CDRpB must survive). The resolver class
is host-swappable via DJANGO_ASSETS_INSTRUMENT_RESOLVER.
"""

import datetime

import pytest
from django.test import override_settings

from django_assets.core.exceptions import (
    AmbiguousInstrumentError,
    InstrumentNotFoundError,
)
from django_assets.core.models import Exchange, Identifier, Instrument
from django_assets.core.resolver import DefaultInstrumentResolver

pytestmark = pytest.mark.django_db


@pytest.fixture
def nyse():
    return Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")


@pytest.fixture
def bcba():
    return Exchange.objects.create(
        code="XBUE", name="Bolsa de Buenos Aires", timezone="America/Argentina/Buenos_Aires"
    )


@pytest.fixture
def aapl(nyse):
    inst = Instrument.objects.create(code="AAPL", quantity_decimals=0)
    Identifier.objects.create(instrument=inst, type="ticker", value="AAPL", exchange=nyse)
    Identifier.objects.create(instrument=inst, type="isin", value="US0378331005")
    return inst


@pytest.fixture
def cedear_aapl(bcba):
    """Same ticker on a different exchange — the ambiguity case."""
    inst = Instrument.objects.create(code="AAPL.BA", quantity_decimals=0)
    Identifier.objects.create(instrument=inst, type="ticker", value="AAPL", exchange=bcba)
    return inst


@pytest.fixture
def cdr_pref(nyse):
    inst = Instrument.objects.create(code="CDRpB", quantity_decimals=0)
    Identifier.objects.create(instrument=inst, type="ticker", value="CDRpB", exchange=nyse)
    return inst


def test_one_match_returns_instrument(aapl):
    assert Instrument.resolve("AAPL") == aapl


def test_zero_matches_raises_not_found(aapl):
    with pytest.raises(InstrumentNotFoundError) as exc_info:
        Instrument.resolve("MSFT")
    assert exc_info.value.value == "MSFT"
    assert exc_info.value.type == "ticker"
    assert exc_info.value.exchange is None


def test_multi_match_raises_ambiguous_with_candidates(aapl, cedear_aapl):
    with pytest.raises(AmbiguousInstrumentError) as exc_info:
        Instrument.resolve("AAPL")
    assert sorted(i.code for i in exc_info.value.candidates) == ["AAPL", "AAPL.BA"]


def test_exchange_filter_disambiguates(aapl, cedear_aapl, nyse, bcba):
    assert Instrument.resolve("AAPL", exchange=nyse) == aapl
    assert Instrument.resolve("AAPL", exchange=bcba) == cedear_aapl


def test_exchange_filter_includes_global_identifiers(aapl, nyse):
    """ADR-0009 step 2: exchange=X means `exchange=X OR exchange IS NULL`."""
    assert Instrument.resolve("US0378331005", type="isin", exchange=nyse) == aapl


# ADR-0018 normalization table, verbatim.
def test_whitespace_stripped(aapl):
    assert Instrument.resolve(" AAPL ") == aapl


def test_lowercase_is_a_miss(aapl):
    with pytest.raises(InstrumentNotFoundError):
        Instrument.resolve("aapl")


def test_preferred_share_case_preserved(cdr_pref):
    assert Instrument.resolve("CDRpB") == cdr_pref
    with pytest.raises(InstrumentNotFoundError):
        Instrument.resolve("CDRPB")


def test_as_of_historical_resolution(nyse):
    """ADR-0009: FISV renamed to FI — historical imports resolve by date."""
    fiserv = Instrument.objects.create(code="FI", quantity_decimals=0)
    Identifier.objects.create(instrument=fiserv, type="ticker", value="FISV", exchange=nyse)
    fiserv.rename_identifier("FISV", "FI", on=datetime.date(2023, 6, 6))

    assert Instrument.resolve("FI") == fiserv
    with pytest.raises(InstrumentNotFoundError):
        Instrument.resolve("FISV")  # no longer active
    assert Instrument.resolve("FISV", as_of=datetime.date(2019, 1, 15)) == fiserv
    assert Instrument.resolve("FI", as_of=datetime.date(2024, 1, 15)) == fiserv
    with pytest.raises(InstrumentNotFoundError):
        Instrument.resolve("FI", as_of=datetime.date(2019, 1, 15))


def test_search_returns_all_candidates(aapl, cedear_aapl):
    results = Instrument.search("AAPL")
    assert sorted(i.code for i in results) == ["AAPL", "AAPL.BA"]


def test_search_returns_empty_list_on_miss(aapl):
    assert Instrument.search("MSFT") == []


def test_resolver_is_read_only(aapl):
    """No resolve_or_create: a miss never creates reference data (ADR-0018)."""
    before = Instrument.objects.count()
    with pytest.raises(InstrumentNotFoundError):
        Instrument.resolve("MSFT")
    assert Instrument.objects.count() == before


class UppercasingResolver(DefaultInstrumentResolver):
    """The documented one-class host override (ADR-0018)."""

    def normalize(self, value: str) -> str:
        return value.strip().upper()


@override_settings(
    DJANGO_ASSETS_INSTRUMENT_RESOLVER=f"{UppercasingResolver.__module__}.UppercasingResolver"
)
def test_resolver_override_is_honored(aapl):
    assert Instrument.resolve("aapl") == aapl
