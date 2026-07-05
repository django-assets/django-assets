"""I2 golden-leg tests: the dividend family (instruments spec §3.3)."""

from decimal import Decimal

import pytest

from django_assets.instruments.equities import templates

from .conftest import TS, legs_by

pytestmark = pytest.mark.ledger

D = Decimal


def test_dividend_received(accounts, usd, aapl):
    tx = templates.dividend_received(
        accounts=accounts, instrument=aapl, amount="32.00", timestamp=TS
    )
    assert legs_by(tx) == {
        ("cash", "USD"): D("32.00"),
        ("issuers", "USD"): D("-32.00"),
    }
    assert "AAPL" in tx.description


def test_dividend_received_with_tax(accounts, usd, aapl):
    tx = templates.dividend_received_with_tax(
        accounts=accounts, instrument=aapl, amount="100.00", tax_withheld="15.00", timestamp=TS
    )
    assert legs_by(tx) == {
        ("cash", "USD"): D("85.00"),
        ("tax_withheld", "USD"): D("15.00"),
        ("issuers", "USD"): D("-100.00"),
    }


def test_foreign_dividend_received(accounts, usd, aapl):
    tx = templates.foreign_dividend_received(
        accounts=accounts, instrument=aapl, amount="100.00", tax_withheld="30.00", timestamp=TS
    )
    assert legs_by(tx) == {
        ("cash", "USD"): D("70.00"),
        ("foreign_tax", "USD"): D("30.00"),
        ("issuers", "USD"): D("-100.00"),
    }


def test_dividend_reinvested(accounts, usd):
    """DRIP = two Transactions (ADR-0021 source shape: brokers post the
    dividend and the reinvestment purchase as separate lines) — and lots
    needs a real purchase leg to open the new lot at the cash basis."""
    from django_assets.core.models import Instrument

    fund = Instrument.objects.create(
        code="VTI", quantity_decimals=4, price_decimals=4, price_currency=usd
    )
    dividend, purchase = templates.dividend_reinvested(
        accounts=accounts, instrument=fund, amount="50.00", quantity="0.1750", timestamp=TS
    )
    assert legs_by(dividend) == {
        ("cash", "USD"): D("50.00"),
        ("issuers", "USD"): D("-50.00"),
    }
    assert legs_by(purchase) == {
        ("holdings", "VTI"): D("0.1750"),
        ("market", "VTI"): D("-0.1750"),
        ("cash", "USD"): D("-50.00"),
        ("market", "USD"): D("50.00"),
    }


def test_capital_gain_distribution(accounts, usd, aapl):
    tx = templates.capital_gain_distribution(
        accounts=accounts, instrument=aapl, amount="12.34", timestamp=TS
    )
    assert legs_by(tx)[("cash", "USD")] == D("12.34")
