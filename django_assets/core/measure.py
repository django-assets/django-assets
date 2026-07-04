"""Measure: an exact amount of a unit of value (core spec §7, ADR-0013).

Frozen value type. Same-unit arithmetic is exact Decimal arithmetic;
cross-unit arithmetic raises UnitMismatchError — there is no implicit FX
anywhere in the ledger. `value()` computes qty × price × multiplier in the
instrument's price_currency, quantized to its price_decimals.
"""

from dataclasses import dataclass
from decimal import Decimal

from django_assets.core.exceptions import UnitMismatchError
from django_assets.core.intake import to_decimal
from django_assets.core.models import Instrument


@dataclass(frozen=True, slots=True)
class Measure:
    amount: Decimal
    unit: Instrument

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))

    def _check_unit(self, other: "Measure") -> None:
        if other.unit.pk != self.unit.pk:
            raise UnitMismatchError(
                f"cannot combine {self.unit.code} with {other.unit.code}: "
                f"no implicit FX (ADR-0013) — convert explicitly first"
            )

    def __add__(self, other: "Measure") -> "Measure":
        self._check_unit(other)
        return Measure(self.amount + other.amount, self.unit)

    def __sub__(self, other: "Measure") -> "Measure":
        self._check_unit(other)
        return Measure(self.amount - other.amount, self.unit)

    def __neg__(self) -> "Measure":
        return Measure(-self.amount, self.unit)

    def __mul__(self, scalar: Decimal | int | str) -> "Measure":
        return Measure(self.amount * to_decimal(scalar, param="scalar"), self.unit)

    __rmul__ = __mul__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Measure):
            return NotImplemented
        return self.unit.pk == other.unit.pk and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.unit.pk))

    def __str__(self) -> str:
        return f"{self.amount} {self.unit.code}"


def value(qty: Decimal | int | str, price: Decimal | int | str, instrument: Instrument) -> Measure:
    """qty × price × multiplier, in price_currency, quantized to price_decimals."""
    if instrument.price_currency is None:
        raise ValueError(
            f"instrument {instrument.code!r} has no price_currency; valuation is undefined"
        )
    raw = to_decimal(qty, param="qty") * to_decimal(price, param="price") * instrument.multiplier
    return Measure(instrument.quantize_price(raw), instrument.price_currency)
