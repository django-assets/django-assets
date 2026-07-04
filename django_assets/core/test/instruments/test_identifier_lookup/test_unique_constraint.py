"""Identifier partial unique constraint (ADR-0009; core spec 2.3).

Only one ACTIVE identifier per (type, value, exchange); inactive
(historical) rows may share values freely — that's how ticker reuse works.
Global (NULL-exchange) identifiers get their own partial constraint because
PG 12 lacks NULLS NOT DISTINCT (ADR-0002 deny-list).
"""

import pytest
from django.db import IntegrityError, transaction

from django_assets.core.models import Exchange, Identifier, Instrument

pytestmark = pytest.mark.django_db


@pytest.fixture
def exchange():
    return Exchange.objects.create(code="XNAS", name="Nasdaq", timezone="America/New_York")


@pytest.fixture
def instrument():
    return Instrument.objects.create(code="AAPL")


def test_duplicate_active_identifier_rejected(exchange, instrument):
    Identifier.objects.create(instrument=instrument, type="ticker", value="AAPL", exchange=exchange)
    other = Instrument.objects.create(code="OTHER")
    with pytest.raises(IntegrityError), transaction.atomic():
        Identifier.objects.create(instrument=other, type="ticker", value="AAPL", exchange=exchange)


def test_inactive_duplicates_allowed_ticker_reuse(exchange, instrument):
    """A defunct company's ticker can be reassigned (ADR-0009 scenario)."""
    Identifier.objects.create(
        instrument=instrument, type="ticker", value="OLD", exchange=exchange, is_active=False
    )
    successor = Instrument.objects.create(code="NEWCO")
    Identifier.objects.create(
        instrument=successor, type="ticker", value="OLD", exchange=exchange, is_active=True
    )
    assert Identifier.objects.filter(value="OLD").count() == 2


def test_duplicate_active_global_identifier_rejected(instrument):
    """NULL-exchange (ISIN/CUSIP) rows must also be unique while active."""
    Identifier.objects.create(instrument=instrument, type="isin", value="US0378331005")
    other = Instrument.objects.create(code="OTHER")
    with pytest.raises(IntegrityError), transaction.atomic():
        Identifier.objects.create(instrument=other, type="isin", value="US0378331005")


def test_same_value_different_exchange_allowed(exchange, instrument):
    nyse = Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")
    other = Instrument.objects.create(code="F")
    Identifier.objects.create(instrument=instrument, type="ticker", value="F", exchange=exchange)
    Identifier.objects.create(instrument=other, type="ticker", value="F", exchange=nyse)
    assert Identifier.objects.filter(value="F").count() == 2
