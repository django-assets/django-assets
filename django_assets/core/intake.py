"""Runtime numeric intake guard (Process ADR-0006 Rule 3).

The primary host runs no type checker and floats are its wire convention,
so every public API that accepts an amount converts here first: Decimal,
int, and str pass through `Decimal(value)` exactly; float fails loudly
with the remedy in the message — before any quantization can hide it.
"""

from decimal import Decimal, InvalidOperation


def to_decimal(value: Decimal | int | str, *, param: str = "amount") -> Decimal:
    if isinstance(value, float):
        raise TypeError(
            f"{param} must be a Decimal, int, or str — got float {value!r}. "
            f'Floats lose precision; pass Decimal(str(value)) or the string "{value}".'
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{param} is not a valid decimal literal: {value!r}") from exc
    raise TypeError(f"{param} must be a Decimal, int, or str — got {type(value).__name__}")
