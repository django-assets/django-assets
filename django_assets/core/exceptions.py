"""Core exception types (core spec section 11)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django_assets.core.models import Instrument


class ExcessPrecisionError(ValueError):
    """An amount carries more precision than the instrument allows.

    Raised by strict quantization on ledger-write paths (D-5): amounts are
    never silently rounded on their way into the ledger.
    """


class UnbalancedTransactionError(ValueError):
    """Per-instrument leg sums are non-zero (Python fallback path).

    Raised by TransactionBuilder before COMMIT when
    DJANGO_ASSETS_USE_DB_TRIGGERS=False. With triggers on, the same
    violation surfaces as IntegrityError at COMMIT instead [D-9].
    """


class MixedOwnershipError(ValueError):
    """A leg references an account with a different owner than the
    transaction's account (invariant D-3; builder-enforced, not DB-enforced).
    """


class UnitMismatchError(TypeError):
    """Arithmetic between Measures of different units — there is no
    implicit FX anywhere in the ledger (ADR-0013).
    """


class InstrumentNotFoundError(Exception):
    """resolve() found no matching Identifier (ADR-0018 shape)."""

    def __init__(self, value: str, type: str, exchange: object = None) -> None:
        self.value = value
        self.type = type
        self.exchange = exchange
        super().__init__(f"No Instrument matching {type}={value!r} exchange={exchange}")


class AmbiguousInstrumentError(Exception):
    """resolve() matched several instruments; carries the candidates so
    callers can recover without a second query (ADR-0018 shape).
    """

    def __init__(self, value: str, candidates: "list[Instrument]") -> None:
        self.value = value
        self.candidates = candidates
        super().__init__(
            f"{value!r} matches {len(candidates)} instruments: "
            f"{[i.code for i in candidates]}. Pass exchange= to disambiguate."
        )
