"""Robinhood built-in schemas (append-only; new formats = new versions)."""

from django_assets.brokerage.schemas.builtin.robinhood.activity_csv_2020 import (
    RobinhoodActivityCsv2020,
)
from django_assets.brokerage.schemas.builtin.robinhood.statement_pdf_2020 import (
    RobinhoodStatementPdf2020,
)

__all__ = ["RobinhoodActivityCsv2020", "RobinhoodStatementPdf2020"]
