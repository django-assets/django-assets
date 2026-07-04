"""Core exception types (core spec section 11)."""


class ExcessPrecisionError(ValueError):
    """An amount carries more precision than the instrument allows.

    Raised by strict quantization on ledger-write paths (D-5): amounts are
    never silently rounded on their way into the ledger.
    """
