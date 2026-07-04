"""Core exception types (core spec section 11)."""


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
