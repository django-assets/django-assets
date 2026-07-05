"""I3 golden-leg tests: option lifecycle (spec §3.4).

The ADR-0020 HIMS short-call roundtrip is the sell_option golden.
Exercise is deliverable-driven with the lookup keyed on trade_timestamp
falling back to timestamp (ADR-0012 — the PFE1 cutover golden test).
Exercise/assignment write the ADR-0032 §3 rollover tag lots materializes
ExerciseLink from.
"""

from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.instruments.options import templates

from .conftest import legs_by, ts

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def hims_call(usd):
    hims = Instrument.objects.create(
        code="HIMS", quantity_decimals=0, price_decimals=4, price_currency=usd
    )
    option = Instrument.objects.create(
        code="HIMS 261218C00030000",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    from django_assets.instruments.options.models import OptionMeta

    OptionMeta.objects.create(
        instrument=option,
        underlying=hims,
        expiry=ts(2026, 12, 18).date(),
        strike=D("30"),
        right="C",
    )
    return option


def test_sell_option_hims_golden(accounts, usd, hims_call):
    """ADR-0020 T1: 2 contracts at $7.85, $0.90 commission, $0.06 fee."""
    tx = templates.sell_option(
        accounts=accounts,
        instrument=hims_call,
        contracts="2",
        price="7.85",
        commission="0.90",
        regulatory_fee="0.06",
        timestamp=ts(2026, 3, 13),
    )
    assert legs_by(tx) == {
        ("holdings", "HIMS 261218C00030000"): D("-2"),
        ("market", "HIMS 261218C00030000"): D("2"),
        ("cash", "USD"): D("1569.04"),
        ("commissions", "USD"): D("0.90"),
        ("regulatory_fees", "USD"): D("0.06"),
        ("market", "USD"): D("-1570.00"),
    }


def test_buy_option_golden(accounts, usd, hims_call):
    """ADR-0020 T2: buy back 2 contracts for $1,000 all-in."""
    tx = templates.buy_option(
        accounts=accounts,
        instrument=hims_call,
        contracts="2",
        price="5.00",
        timestamp=ts(2026, 4, 2),
    )
    assert legs_by(tx) == {
        ("holdings", "HIMS 261218C00030000"): D("2"),
        ("market", "HIMS 261218C00030000"): D("-2"),
        ("cash", "USD"): D("-1000.00"),
        ("market", "USD"): D("1000.00"),
    }


def test_exercise_uses_adjusted_basket_on_cutover(accounts, usd, pfe, vtrs, pfe1_call):
    """PFE1 golden: trade_timestamp 2020-11-17 → 100 PFE + 12 VTRS + $6.47,
    plus the $3,500 strike payment (call, $35 × 100)."""
    tx = templates.exercise_option(
        accounts=accounts,
        instrument=pfe1_call,
        contracts="1",
        timestamp=ts(2020, 11, 19),  # settles T+2
        trade_timestamp=ts(2020, 11, 17),
    )
    assert legs_by(tx) == {
        ("holdings", "PFE1 201218C00035000"): D("-1"),
        ("market", "PFE1 201218C00035000"): D("1"),
        ("holdings", "PFE"): D("100"),
        ("market", "PFE"): D("-100"),
        ("holdings", "VTRS"): D("12"),
        ("market", "VTRS"): D("-12"),
        # $6.47 deliverable cash in, $3,500 strike out: net −3,493.53.
        ("cash", "USD"): D("-3493.53"),
        ("market", "USD"): D("3493.53"),
    }
    tag = tx.metadata["rollover"]
    assert tag["kind"] == "exercise"
    assert tag["option_instrument_id"] == pfe1_call.pk
    assert tag["underlying_instrument_id"] == pfe.pk
    assert tag["contracts"] == "1"
    assert tag["strike"] == "35"
    assert tag["multiplier"] == "100"


def test_exercise_before_cutover_uses_original(accounts, usd, pfe, pfe1_call):
    tx = templates.exercise_option(
        accounts=accounts,
        instrument=pfe1_call,
        contracts="1",
        timestamp=ts(2020, 11, 18),
        trade_timestamp=ts(2020, 11, 16),
    )
    legs = legs_by(tx)
    assert legs[("holdings", "PFE")] == D("100")
    assert ("holdings", "VTRS") not in legs
    assert legs[("cash", "USD")] == D("-3500.00")


def test_exercise_null_trade_timestamp_falls_back(accounts, usd, pfe, pfe1_call):
    """No trade_timestamp: deliverable lookup keys on timestamp itself."""
    tx = templates.exercise_option(
        accounts=accounts,
        instrument=pfe1_call,
        contracts="1",
        timestamp=ts(2020, 11, 16),
    )
    assert ("holdings", "VTRS") not in legs_by(tx)


def test_exercise_override_deliverables(accounts, usd, pfe, pfe1_call):
    """Broker-statement import path: caller supplies the basket."""
    tx = templates.exercise_option(
        accounts=accounts,
        instrument=pfe1_call,
        contracts="2",
        timestamp=ts(2020, 11, 20),
        override_deliverables=[{"instrument": pfe, "quantity": "100"}],
    )
    legs = legs_by(tx)
    assert legs[("holdings", "PFE")] == D("200")  # 2 contracts × 100
    assert legs[("cash", "USD")] == D("-7000.00")  # strike only, no cash component


def test_assign_option_put_golden(accounts, usd):
    """ADR-0032 §3 example: short $10 put assigned — receive 100 shares,
    pay $1,000; the rollover tag lets lots set basis $9.50/sh after the
    $0.50 premium."""
    xyz = Instrument.objects.create(
        code="XYZ", quantity_decimals=0, price_decimals=4, price_currency=usd
    )
    put = Instrument.objects.create(
        code="XYZ 260618P00010000",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    from django_assets.instruments.options.models import Deliverable, OptionMeta

    meta = OptionMeta.objects.create(
        instrument=put,
        underlying=xyz,
        expiry=ts(2026, 6, 18).date(),
        strike=D("10"),
        right="P",
    )
    Deliverable.objects.create(
        option_meta=meta,
        instrument=xyz,
        quantity=D("100"),
        effective_from=ts(2026, 1, 2).date(),
    )
    tx = templates.assign_option(
        accounts=accounts, instrument=put, contracts="1", timestamp=ts(2026, 5, 15)
    )
    assert legs_by(tx) == {
        ("holdings", "XYZ 260618P00010000"): D("1"),  # short position closes
        ("market", "XYZ 260618P00010000"): D("-1"),
        ("holdings", "XYZ"): D("100"),  # put assigned: shares come IN
        ("market", "XYZ"): D("-100"),
        ("cash", "USD"): D("-1000.00"),  # pay the strike
        ("market", "USD"): D("1000.00"),
    }
    tag = tx.metadata["rollover"]
    assert tag["kind"] == "assignment"
    assert tag["strike"] == "10"


def test_assign_option_call_delivers_shares(accounts, usd, pfe, pfe1_call):
    """Short call assigned: shares go OUT, strike cash comes IN."""
    tx = templates.assign_option(
        accounts=accounts,
        instrument=pfe1_call,
        contracts="1",
        timestamp=ts(2020, 11, 16),
        trade_timestamp=ts(2020, 11, 16),
    )
    legs = legs_by(tx)
    assert legs[("holdings", "PFE")] == D("-100")
    assert legs[("cash", "USD")] == D("3500.00")
    assert legs[("holdings", "PFE1 201218C00035000")] == D("1")


def test_expire_option(accounts, usd, pfe1_call):
    """Signed contracts close the position; no cash moves."""
    long_close = templates.expire_option(
        accounts=accounts, instrument=pfe1_call, contracts="2", timestamp=ts(2020, 12, 19)
    )
    assert legs_by(long_close) == {
        ("holdings", "PFE1 201218C00035000"): D("-2"),
        ("market", "PFE1 201218C00035000"): D("2"),
    }
    short_close = templates.expire_option(
        accounts=accounts, instrument=pfe1_call, contracts="-2", timestamp=ts(2020, 12, 19)
    )
    assert legs_by(short_close)[("holdings", "PFE1 201218C00035000")] == D("2")


def test_exercise_without_deliverables_raises(accounts, usd):
    naked = Instrument.objects.create(
        code="NODELIV 260101C00001000",
        quantity_decimals=0,
        multiplier=D("100"),
        price_currency=usd,
    )
    from django_assets.instruments.options.models import OptionMeta

    OptionMeta.objects.create(
        instrument=naked,
        underlying=usd,
        expiry=ts(2026, 1, 1).date(),
        strike=D("1"),
        right="C",
    )
    with pytest.raises(ValueError, match="deliverable"):
        templates.exercise_option(
            accounts=accounts, instrument=naked, contracts="1", timestamp=ts(2025, 12, 1)
        )
