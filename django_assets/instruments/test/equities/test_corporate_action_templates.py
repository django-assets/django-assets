"""I2 golden-leg + tag-shape tests: corporate actions (spec §3.3).

Per-account recordings, no fan-out (ADR-0011). Every template writes the
ADR-0032 §6 corporate_action metadata tag that the lots rebuild
interprets; the 4:1 stock_split tag here is the write-side golden for
lots L1's consumption test.
"""

from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.instruments.equities import templates

from .conftest import TS, legs_by

pytestmark = pytest.mark.ledger

D = Decimal


def test_stock_split_golden(accounts, usd, aapl):
    """4:1 split on 100 held shares: +300 arrive; the tag drives lots."""
    tx = templates.stock_split(
        accounts=accounts, instrument=aapl, additional_quantity="300", ratio="4", timestamp=TS
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("300"),
        ("issuers", "AAPL"): D("-300"),
    }
    assert tx.metadata["corporate_action"] == {
        "type": "split",
        "ratio": "4",
        "instrument_id": aapl.pk,
    }


def test_reverse_split_golden(accounts, usd, aapl):
    """1-for-10: 1000 shares become 100; ratio is the to/from multiplier."""
    tx = templates.reverse_split(
        accounts=accounts,
        instrument=aapl,
        removed_quantity="900",
        ratio="0.1",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("-900"),
        ("issuers", "AAPL"): D("900"),
    }
    assert tx.metadata["corporate_action"]["type"] == "reverse_split"
    assert tx.metadata["corporate_action"]["ratio"] == "0.1"


def test_stock_dividend_golden(accounts, usd, aapl):
    tx = templates.stock_dividend(
        accounts=accounts, instrument=aapl, additional_quantity="5", ratio="1.05", timestamp=TS
    )
    assert legs_by(tx)[("holdings", "AAPL")] == D("5")
    assert tx.metadata["corporate_action"]["type"] == "stock_dividend"


def test_spinoff_golden(accounts, usd, aapl):
    spinco = Instrument.objects.create(code="SPINCO", quantity_decimals=0, price_currency=usd)
    tx = templates.spinoff(
        accounts=accounts,
        instrument=aapl,
        new_instrument=spinco,
        quantity="12",
        ratio="0.12",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "SPINCO"): D("12"),
        ("issuers", "SPINCO"): D("-12"),
    }
    tag = tx.metadata["corporate_action"]
    assert tag["type"] == "spinoff"
    assert tag["instrument_id"] == aapl.pk
    assert tag["new_instrument_id"] == spinco.pk


def test_merger_exchange_golden(accounts, usd, aapl):
    acquirer = Instrument.objects.create(code="ACQ", quantity_decimals=0, price_currency=usd)
    tx = templates.merger_exchange(
        accounts=accounts,
        instrument=aapl,
        new_instrument=acquirer,
        quantity="100",
        new_quantity="25",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("-100"),
        ("issuers", "AAPL"): D("100"),
        ("holdings", "ACQ"): D("25"),
        ("issuers", "ACQ"): D("-25"),
    }
    tag = tx.metadata["corporate_action"]
    assert tag["type"] == "merger_exchange"
    assert tag["new_instrument_id"] == acquirer.pk


def test_rights_offering_golden(accounts, usd, aapl):
    rights = Instrument.objects.create(code="AAPL.RT", quantity_decimals=0, price_currency=usd)
    tx = templates.rights_offering(
        accounts=accounts, instrument=aapl, rights_instrument=rights, quantity="100", timestamp=TS
    )
    assert legs_by(tx)[("holdings", "AAPL.RT")] == D("100")
    assert tx.metadata["corporate_action"]["type"] == "rights_offering"


def test_warrant_exercise_golden(accounts, usd, aapl):
    warrant = Instrument.objects.create(code="AAPL.WS", quantity_decimals=0, price_currency=usd)
    tx = templates.warrant_exercise(
        accounts=accounts,
        warrant_instrument=warrant,
        instrument=aapl,
        quantity="10",
        cost="115.00",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL.WS"): D("-10"),
        ("issuers", "AAPL.WS"): D("10"),
        ("holdings", "AAPL"): D("10"),
        ("issuers", "AAPL"): D("-10"),
        ("cash", "USD"): D("-115.00"),
        ("issuers", "USD"): D("115.00"),
    }
    assert tx.metadata["corporate_action"]["type"] == "warrant_exercise"
