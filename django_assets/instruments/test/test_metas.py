"""I1: CorporateAction + CurrencyMeta/CryptoMeta — instruments spec §2/§3.

Categorization is the PRESENCE of a meta row; there is no kind enum
anywhere (ADR-0013/0033). CorporateAction sits at the package root
because actions cross types.
"""

import datetime

import pytest
from django.db import IntegrityError

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.crypto.models import CryptoMeta
from django_assets.instruments.currencies.models import CurrencyMeta
from django_assets.instruments.models import CorporateAction

pytestmark = pytest.mark.django_db


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def usdc(usd):
    return Instrument.objects.create(code="USDC", quantity_decimals=6)


def test_currency_meta_one_to_one(usd):
    CurrencyMeta.objects.create(
        instrument=usd, iso_code="USD", iso_numeric=840, symbol="$", is_fiat=True
    )
    assert usd.currency_meta.iso_code == "USD"
    with pytest.raises(IntegrityError):
        CurrencyMeta.objects.create(instrument=usd, iso_code="US2", iso_numeric=841)


def test_iso_code_unique(usd, usdc):
    CurrencyMeta.objects.create(instrument=usd, iso_code="USD", iso_numeric=840)
    with pytest.raises(IntegrityError):
        CurrencyMeta.objects.create(instrument=usdc, iso_code="USD", iso_numeric=840)


def test_stablecoin_pegged_to_chain(usd, usdc):
    """USDC (crypto) pegs to the USD Instrument — cross-type via core FK."""
    CurrencyMeta.objects.create(instrument=usd, iso_code="USD", iso_numeric=840)
    meta = CryptoMeta.objects.create(
        instrument=usdc,
        symbol="USDC",
        network="ethereum",
        is_stablecoin=True,
        pegged_to=usd,
    )
    assert meta.pegged_to == usd
    assert meta.pegged_to.currency_meta.iso_code == "USD"


def test_pegged_to_is_protected(usd, usdc):
    CryptoMeta.objects.create(
        instrument=usdc, symbol="USDC", network="ethereum", is_stablecoin=True, pegged_to=usd
    )
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        usd.delete()


def test_categorization_by_presence(usd, usdc):
    """The documented host idiom: filter on meta-row existence."""
    CurrencyMeta.objects.create(instrument=usd, iso_code="USD", iso_numeric=840)
    CryptoMeta.objects.create(instrument=usdc, symbol="USDC", network="ethereum")
    currencies = Instrument.objects.filter(currency_meta__isnull=False)
    cryptos = Instrument.objects.filter(crypto_meta__isnull=False)
    assert list(currencies) == [usd]
    assert list(cryptos) == [usdc]
    assert not hasattr(Instrument, "kind")  # no discriminator, ever


def test_corporate_action_and_identifier_linkage(usd):
    """Core works with the FK null; linkage round-trips when populated."""
    ident = Identifier.objects.create(instrument=usd, type="ticker", value="USD")
    assert ident.corporate_action is None

    action = CorporateAction.objects.create(
        effective_date=datetime.date(2023, 6, 6),
        action_type="symbol_change",
        source_reference="TEST #1",
        description="FISV renamed to FI",
        primary_instrument=usd,
    )
    ident.corporate_action = action
    ident.save(update_fields=["corporate_action"])
    ident.refresh_from_db()
    assert ident.corporate_action == action
    assert action.identifiers.count() == 1


def test_corporate_action_primary_instrument_set_null(usd):
    action = CorporateAction.objects.create(
        effective_date=datetime.date(2024, 1, 2),
        action_type="delisting",
        primary_instrument=usd,
    )
    # SET_NULL both ways: deleting the instrument keeps the action row.
    Identifier.objects.all().delete()
    usd.delete()
    action.refresh_from_db()
    assert action.primary_instrument is None
