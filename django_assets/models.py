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
from django_assets.instruments.crypto.models import CryptoMeta
from django_assets.instruments.currencies.models import CurrencyMeta
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import Deliverable, OptionMeta

__all__ = [
    "Account",
    "CorporateAction",
    "CryptoMeta",
    "CurrencyMeta",
    "Deliverable",
    "EquityMeta",
    "Exchange",
    "Identifier",
    "Instrument",
    "OptionMeta",
    "Transaction",
    "TransactionLeg",
]
