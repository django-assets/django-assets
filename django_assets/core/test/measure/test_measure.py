"""C3: Measure + value() — spec §7.

Frozen value type with exact same-unit arithmetic, no implicit FX
(ADR-0013), and valuation via qty × price × multiplier quantized to the
instrument's price_decimals in price_currency units.
"""

from decimal import Decimal

import pytest

from django_assets.core.exceptions import UnitMismatchError
from django_assets.core.measure import Measure, value
from django_assets.core.models import Instrument

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def eur():
    return Instrument.objects.create(code="EUR", quantity_decimals=2)


@pytest.fixture
def aapl(usd):
    return Instrument.objects.create(
        code="AAPL", quantity_decimals=0, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def spy_call(usd):
    return Instrument.objects.create(
        code="SPY260618C600",
        quantity_decimals=0,
        price_decimals=2,
        multiplier=D("100"),
        price_currency=usd,
    )


def test_same_unit_arithmetic_exact(usd):
    a = Measure(D("0.10"), usd)
    b = Measure(D("0.20"), usd)
    assert (a + b).amount == D("0.30")  # the float-classic, exact here
    assert (b - a).amount == D("0.10")
    assert (-a).amount == D("-0.10")
    assert (a + b).unit == usd


def test_cross_unit_arithmetic_raises(usd, eur):
    with pytest.raises(UnitMismatchError):
        Measure(D("1"), usd) + Measure(D("1"), eur)
    with pytest.raises(UnitMismatchError):
        Measure(D("1"), usd) - Measure(D("1"), eur)


def test_equality_and_hash(usd, eur):
    assert Measure(D("1.00"), usd) == Measure(D("1.00"), usd)
    assert Measure(D("1.00"), usd) != Measure(D("1.00"), eur)
    assert hash(Measure(D("1.00"), usd)) == hash(Measure(D("1.00"), usd))


def test_measure_is_frozen(usd):
    m = Measure(D("1.00"), usd)
    with pytest.raises(AttributeError):
        m.amount = D("2.00")


def test_scalar_multiplication(usd):
    m = Measure(D("1.50"), usd)
    assert (m * 3).amount == D("4.50")
    assert (m * D("0.5")).amount == D("0.750")


def test_float_intake_rejected(usd):
    """PADR-0006 Rule 3 applies to the value types too."""
    with pytest.raises(TypeError, match="Decimal"):
        Measure(1.1, usd)  # float-ok
    with pytest.raises(TypeError, match="Decimal"):
        Measure(D("1"), usd) * 1.1  # float-ok


def test_str_and_int_intake_exact(usd):
    assert Measure("1.10", usd).amount == D("1.10")
    assert Measure(3, usd).amount == D("3")


def test_value_share_quantity_times_price(aapl, usd):
    m = value(D("100"), D("175.5056"), aapl)
    assert m.unit == usd
    assert m.amount == D("17550.56")  # quantized to price_decimals=2


def test_value_applies_multiplier(spy_call, usd):
    # 3 contracts at $2.50, multiplier 100 → $750.00
    m = value(D("3"), D("2.50"), spy_call)
    assert m.unit == usd
    assert m.amount == D("750.00")


def test_value_rejects_floats(aapl):  # float-ok
    with pytest.raises(TypeError, match="Decimal"):
        value(100.0, D("175.50"), aapl)  # float-ok
    with pytest.raises(TypeError, match="Decimal"):
        value(D("100"), 175.50, aapl)  # float-ok


def test_value_requires_price_currency(usd):
    """A currency itself has no price_currency; valuation is undefined."""
    with pytest.raises(ValueError, match="price_currency"):
        value(D("1"), D("1"), usd)
