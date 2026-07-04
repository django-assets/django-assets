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

__all__ = [
    "AccountAdmin",
    "ExchangeAdmin",
    "IdentifierAdmin",
    "InstrumentAdmin",
    "TransactionAdmin",
    "TransactionLegAdmin",
]
