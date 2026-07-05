"""I2: convert_instrument — the ADR-0009 four-leg DTC shape (CEDEAR ↔
ordinary), ratio-checked, writing the ADR-0032 §5 conversion tag [D-45]
that lots L3 materializes ConversionLink from."""

from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.instruments.equities import templates

from .conftest import TS, legs_by

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def ars():
    return Instrument.objects.create(code="ARS", quantity_decimals=2)


@pytest.fixture
def cedear(ars):
    return Instrument.objects.create(code="AAPL.BA", quantity_decimals=0, price_currency=ars)


def test_convert_instrument_four_leg_shape(accounts, usd, aapl, cedear):
    """200 CEDEARs (10:1) become 20 ordinary shares; no cash, no result."""
    tx = templates.convert_instrument(
        accounts=accounts,
        from_instrument=cedear,
        to_instrument=aapl,
        from_quantity="200",
        to_quantity="20",
        ratio="10",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL.BA"): D("-200"),
        ("issuers", "AAPL.BA"): D("200"),
        ("holdings", "AAPL"): D("20"),
        ("issuers", "AAPL"): D("-20"),
    }
    assert tx.metadata["conversion"] == {
        "from_instrument_id": cedear.pk,
        "to_instrument_id": aapl.pk,
        "from_quantity": "200",
        "to_quantity": "20",
    }


def test_ratio_mismatch_raises(accounts, usd, aapl, cedear):
    with pytest.raises(ValueError, match="ratio"):
        templates.convert_instrument(
            accounts=accounts,
            from_instrument=cedear,
            to_instrument=aapl,
            from_quantity="200",
            to_quantity="19",
            ratio="10",
            timestamp=TS,
        )


def test_ratio_is_optional(accounts, usd, aapl, cedear):
    tx = templates.convert_instrument(
        accounts=accounts,
        from_instrument=cedear,
        to_instrument=aapl,
        from_quantity="10",
        to_quantity="1",
        timestamp=TS,
    )
    assert tx.metadata["conversion"]["from_quantity"] == "10"
