"""Admin aggregator: Django autodiscovers <app>.admin; sub-packages
register here (mirrors models.py)."""

from django_assets.brokerage.admin import (
    AccountProfileAdmin,
    DisclosureEventAdmin,
    ImportBatchAdmin,
    ImportLineAdmin,
    ImportLineProposalAdmin,
    TransactionImportAdmin,
)
from django_assets.core.admin import (
    AccountAdmin,
    ExchangeAdmin,
    IdentifierAdmin,
    InstrumentAdmin,
    TransactionAdmin,
    TransactionLegAdmin,
)
from django_assets.instruments.admin import (
    CorporateActionAdmin,
    CryptoMetaAdmin,
    CurrencyMetaAdmin,
    EquityMetaAdmin,
    OptionMetaAdmin,
)
from django_assets.lots.admin import (
    ConversionLinkAdmin,
    ExerciseLinkAdmin,
    LotAdmin,
    LotEventAdmin,
    LotMatchAdmin,
    WashSaleAdjustmentAdmin,
)
from django_assets.trades.admin import (
    TagAdmin,
    TagCategoryAdmin,
    TradeAdmin,
    TradeAllocationAdmin,
    VirtualTransferAdmin,
)

__all__ = [
    "AccountAdmin",
    "AccountProfileAdmin",
    "CorporateActionAdmin",
    "CryptoMetaAdmin",
    "ConversionLinkAdmin",
    "CurrencyMetaAdmin",
    "DisclosureEventAdmin",
    "EquityMetaAdmin",
    "ExchangeAdmin",
    "ExerciseLinkAdmin",
    "IdentifierAdmin",
    "ImportBatchAdmin",
    "ImportLineAdmin",
    "ImportLineProposalAdmin",
    "LotAdmin",
    "LotEventAdmin",
    "LotMatchAdmin",
    "InstrumentAdmin",
    "OptionMetaAdmin",
    "TagAdmin",
    "TagCategoryAdmin",
    "TradeAdmin",
    "TradeAllocationAdmin",
    "TransactionAdmin",
    "TransactionImportAdmin",
    "VirtualTransferAdmin",
    "WashSaleAdjustmentAdmin",
    "TransactionLegAdmin",
]
