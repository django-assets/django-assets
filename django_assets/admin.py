"""Admin aggregator: Django autodiscovers <app>.admin; sub-packages
register here (mirrors models.py)."""

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
)

__all__ = [
    "AccountAdmin",
    "CorporateActionAdmin",
    "CryptoMetaAdmin",
    "CurrencyMetaAdmin",
    "EquityMetaAdmin",
    "ExchangeAdmin",
    "IdentifierAdmin",
    "InstrumentAdmin",
    "TransactionAdmin",
    "TransactionLegAdmin",
]
