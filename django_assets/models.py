"""Model aggregation for the single django_assets app (ADR-0015).

Django discovers models via <app>.models; each sub-package keeps its own
models module and re-exports here.
"""

from django_assets.brokerage.models import (
    AccountProfile,
    DisclosureEvent,
    ImportBatch,
    ImportLine,
    ImportLineProposal,
    TransactionImport,
)
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
from django_assets.trades.models import Trade, TradeAllocation

__all__ = [
    "Account",
    "AccountProfile",
    "CorporateAction",
    "CryptoMeta",
    "DisclosureEvent",
    "CurrencyMeta",
    "Deliverable",
    "EquityMeta",
    "Exchange",
    "Identifier",
    "ImportBatch",
    "ImportLine",
    "ImportLineProposal",
    "Instrument",
    "OptionMeta",
    "Trade",
    "TradeAllocation",
    "Transaction",
    "TransactionImport",
    "TransactionLeg",
]
