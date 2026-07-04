"""I1: seed fixtures — instruments spec §3.1/§3.2, D-12.

Fixtures load EXPLICITLY (dev project / demos); they are never
auto-seeded into adopter databases. Loading is idempotent.
"""

from decimal import Decimal

import pytest
from django.core.management import call_command

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.crypto import fixtures as crypto_fixtures
from django_assets.instruments.crypto.models import CryptoMeta
from django_assets.instruments.currencies import fixtures as currency_fixtures
from django_assets.instruments.currencies.models import CurrencyMeta

pytestmark = pytest.mark.django_db

D = Decimal


def test_currency_fixtures_load(db):
    currency_fixtures.load()
    assert CurrencyMeta.objects.count() == 10
    by_code = {m.iso_code: m for m in CurrencyMeta.objects.select_related("instrument")}
    assert by_code["USD"].instrument.quantity_decimals == 2
    assert by_code["JPY"].instrument.quantity_decimals == 0
    assert by_code["USD"].is_fiat is True
    # Uppercase global identifiers (ADR-0018 seed convention).
    ident = Identifier.objects.get(type="ticker", value="USD")
    assert ident.exchange is None
    assert ident.instrument == by_code["USD"].instrument


def test_crypto_fixtures_load(db):
    crypto_fixtures.load()
    metas = {m.symbol: m for m in CryptoMeta.objects.select_related("instrument")}
    assert metas["BTC"].instrument.quantity_decimals == 8
    assert metas["ETH"].instrument.quantity_decimals == 18
    # Stablecoins peg to the USD Instrument (created on demand).
    assert metas["USDC"].is_stablecoin is True
    assert metas["USDC"].pegged_to is not None
    assert metas["USDC"].pegged_to.code == "USD"


def test_fixture_load_is_idempotent(db):
    currency_fixtures.load()
    crypto_fixtures.load()
    counts = (Instrument.objects.count(), Identifier.objects.count())
    currency_fixtures.load()
    crypto_fixtures.load()
    assert (Instrument.objects.count(), Identifier.objects.count()) == counts


def test_seed_command_loads_everything(db):
    call_command("seed_instruments")
    assert CurrencyMeta.objects.count() == 10
    assert CryptoMeta.objects.count() == 7
