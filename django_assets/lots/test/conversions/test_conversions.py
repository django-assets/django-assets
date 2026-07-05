"""L3: conversion carryover (ADR-0032 §5) — the CEDEAR golden."""

from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.instruments.equities import templates as eq
from django_assets.lots.models import ConversionLink, Lot, LotMatch
from django_assets.lots.rebuild import rebuild_lots

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def ars():
    return Instrument.objects.create(code="ARS", quantity_decimals=2)


@pytest.fixture
def cedear(ars):
    return Instrument.objects.create(
        code="AAPL.BA", quantity_decimals=0, price_decimals=2, price_currency=ars
    )


@pytest.fixture
def ordinary(usd):
    return Instrument.objects.create(
        code="AAPL.US", quantity_decimals=0, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def cedear_history(accounts, ars, usd, cedear, ordinary, buy, sell):
    """Buy 2,000 CEDEARs for ARS 21M → convert 20:1 → sell 100 ordinaries
    for USD 21,000."""
    buy("2000", "10500.00", at(0), instrument=cedear)  # ARS 21,000,000
    eq.convert_instrument(
        accounts=accounts,
        from_instrument=cedear,
        to_instrument=ordinary,
        from_quantity="2000",
        to_quantity="100",
        ratio="20",
        timestamp=at(30),
    )
    sell("100", "210.00", at(60), instrument=ordinary)  # USD 21,000
    return accounts


def test_conversion_carryover_golden(cedear_history, accounts, ars, cedear, ordinary):
    """Zero realized result at conversion; target lots carry the ARS
    basis ratio-mapped with the ORIGINAL acquisition date; no rate is
    stored anywhere."""
    rebuild_lots(accounts["holdings"])

    assert ConversionLink.objects.filter(source="metadata").exists()
    # The conversion itself realizes nothing.
    conversion_gains = LotMatch.objects.filter(lot__instrument=cedear)
    assert sum(m.realized_gain for m in conversion_gains) == D("0")

    ordinary_lot = Lot.objects.get(instrument=ordinary, quantity__gt=0)
    assert ordinary_lot.cost_basis == D("21000000.00")  # ARS, unchanged
    assert ordinary_lot.acquired_at == at(0)  # holding period tacks
    assert ordinary_lot.metadata.get("basis_currency") == "ARS"

    # The sale reports as an honest currency pair.
    sale_match = LotMatch.objects.get(lot=ordinary_lot)
    assert sale_match.proceeds == D("21000.00")  # USD
    assert sale_match.basis_recovered == D("21000000.00")  # ARS
    assert sale_match.metadata.get("cross_currency") is True
    # Implied rate derives at view time: 21M ARS / 21k USD = 1000 —
    # and no stored field anywhere holds it.
    implied = sale_match.basis_recovered / sale_match.proceeds
    assert implied == D("1000")


def test_unlinked_conversion_falls_back_flagged(cedear_history, accounts, cedear, ordinary):
    from django_assets.core.models import Transaction

    conversion = Transaction.objects.filter(metadata__has_key="conversion").get()
    conversion.metadata = {}
    conversion.save(update_fields=["metadata"])
    rebuild_lots(accounts["holdings"])

    ordinary_lot = Lot.objects.get(instrument=ordinary, quantity__gt=0)
    assert ordinary_lot.metadata.get("unlinked") is True
    assert ordinary_lot.rollover_linked is False


def test_fx_rate_source_is_a_report_parameter(cedear_history, accounts, ars, usd, ordinary):
    """Same stored facts, two stub sources, two different single-currency
    gains — and the rows stay byte-identical across runs."""
    from django_assets.lots.reports import realized_gains

    rebuild_lots(accounts["holdings"])
    rows_before = list(LotMatch.objects.order_by("id").values_list("proceeds", "basis_recovered"))

    class Rate:
        def __init__(self, rate):
            self.rate = rate

        def get_rate(self, base, quote, on):
            return D(self.rate)

    cheap = realized_gains(accounts["holdings"], fx=Rate("500"), currency=usd)
    dear = realized_gains(accounts["holdings"], fx=Rate("2000"), currency=usd)
    gain_cheap = sum(row["realized_gain"] for row in cheap if row["instrument"] == "AAPL.US")
    gain_dear = sum(row["realized_gain"] for row in dear if row["instrument"] == "AAPL.US")
    assert gain_cheap != gain_dear

    rows_after = list(LotMatch.objects.order_by("id").values_list("proceeds", "basis_recovered"))
    assert rows_after == rows_before  # rates never touch storage
