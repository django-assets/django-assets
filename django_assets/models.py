"""Model aggregation for the single django_assets app (ADR-0015).

Django discovers models via <app>.models; each sub-package keeps its own
models module and re-exports here.
"""

from django_assets.core.models import (
    Account,
    Exchange,
    Identifier,
    Instrument,
    Transaction,
    TransactionLeg,
)

__all__ = [
    "Account",
    "Exchange",
    "Identifier",
    "Instrument",
    "Transaction",
    "TransactionLeg",
]
