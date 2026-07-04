"""Admin aggregator: Django autodiscovers <app>.admin; sub-packages
register here (mirrors models.py)."""

from django_assets.brokerage.admin import (
    AccountProfileAdmin,
    ImportBatchAdmin,
    ImportLineAdmin,
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

__all__ = [
    "AccountAdmin",
    "AccountProfileAdmin",
    "CorporateActionAdmin",
    "CryptoMetaAdmin",
    "CurrencyMetaAdmin",
    "EquityMetaAdmin",
    "ExchangeAdmin",
    "IdentifierAdmin",
    "ImportBatchAdmin",
    "ImportLineAdmin",
    "InstrumentAdmin",
    "OptionMetaAdmin",
    "TransactionAdmin",
    "TransactionImportAdmin",
    "TransactionLegAdmin",
]
