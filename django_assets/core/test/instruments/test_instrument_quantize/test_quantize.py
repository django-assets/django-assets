"""Instrument precision rules (core spec 2.2; D-1, D-5).

quantize() rounds computed values HALF_UP; strict=True is the ledger-write
posture: an amount whose value would change is rejected, never silently
truncated.
"""

from decimal import Decimal

import pytest

from django_assets.core.exceptions import ExcessPrecisionError
from django_assets.core.models import Instrument


def make(qd=4, pd=4):
    return Instrument(code="X", quantity_decimals=qd, price_decimals=pd)


def test_precision_defaults():
    inst = Instrument(code="X")
    assert inst.quantity_decimals == 4
    assert inst.price_decimals == 4
    assert inst.multiplier == Decimal("1")
    assert inst.is_active is True


def test_quantize_rounds_half_up():
    inst = make(qd=2)
    assert inst.quantize(Decimal("1.005")) == Decimal("1.01")
    assert inst.quantize(Decimal("1.004")) == Decimal("1.00")
    assert inst.quantize(Decimal("-1.005")) == Decimal("-1.01")


def test_quantize_pads_scale():
    assert make(qd=2).quantize(Decimal("1.2")) == Decimal("1.20")


def test_quantize_strict_accepts_exact_amounts():
    inst = make(qd=2)
    assert inst.quantize(Decimal("1.25"), strict=True) == Decimal("1.25")
    assert inst.quantize(Decimal("7"), strict=True) == Decimal("7.00")


def test_quantize_strict_rejects_excess_precision():
    inst = make(qd=2)
    with pytest.raises(ExcessPrecisionError):
        inst.quantize(Decimal("0.123"), strict=True)


def test_quantize_zero_decimals_jpy_style():
    inst = make(qd=0)
    assert inst.quantize(Decimal("100.5")) == Decimal("101")
    with pytest.raises(ExcessPrecisionError):
        inst.quantize(Decimal("100.5"), strict=True)


def test_quantize_price_uses_price_decimals():
    inst = make(qd=0, pd=3)
    assert inst.quantize_price(Decimal("1.2345")) == Decimal("1.235")
    with pytest.raises(ExcessPrecisionError):
        inst.quantize_price(Decimal("1.2345"), strict=True)
