"""TD Ameritrade built-in schemas (append-only)."""

from django_assets.brokerage.schemas.builtin.tdameritrade.advisor_statement_pdf_2023 import (
    TdAmeritradeAdvisorStatementPdf2023,
)
from django_assets.brokerage.schemas.builtin.tdameritrade.statement_pdf_2012 import (
    TdAmeritradeStatementPdf2012,
)

__all__ = ["TdAmeritradeAdvisorStatementPdf2023", "TdAmeritradeStatementPdf2012"]
