"""Synthetic coverage for the Tradier statement-PDF schema (fabricated
Apex-layout text; the real corpus is private and git-excluded)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

STATEMENT_TEXT = """\
April 1, 2023 - April 30, 2023
ACCOUNT NUMBER 6XX-00000-00 RR TNB
OPENING BALANCE CLOSING BALANCE
Margin account $950.00 $79.14
NET ACCOUNT BALANCE 950.00 79.14 Cash
EQUITIES / OPTIONS
ISHARES TRUST SGOV M 49 $100.56 $4,927.44 N/A $120 98.419%
ISHARES 0 3 MONTH TREASURY
BOND ETF
Total Equities $4,927.44
ACCOUNT
TRANSACTION DATE TYPE DESCRIPTION QUANTITY PRICE DEBIT CREDIT
BUY / SELL TRANSACTIONS
BOUGHT 04/12/23 M ISHARES TRUST 9 $100.3182 $902.86
ISHARES 0 3 MONTH TREASURY
BOND ETF
UNSOLICITED
CUSIP: 46436E718
SOLD 04/25/23 M ISHARES TRUST 2 100.50 201.00
ISHARES 0 3 MONTH TREASURY
BOND ETF
CUSIP: 46436E718
Total Buy / Sell Transactions $902.86 $201.00
DIVIDENDS AND INTEREST
DIVIDEND 04/05/23 M ISHARES TRUST $0.392064 $19.21
ISHARES 0 3 MONTH TREASURY
BOND ETF
CASH DIV ON 49 SHS
REC 04/02/23 PAY 04/05/23
NON-QUALIFIED DIVIDEND
CUSIP: 46436E718
Total Dividends And Interest $19.21
FUNDS PAID AND RECEIVED
ACH 04/11/23 M ACH DEPOSIT $4,050.00
SEN(20230411031505)
ACH 04/28/23 M ACH DISBURSEMENT $500.00
SEN(20230428031505)
Total Funds Paid And Received $4,550.00
MISCELLANEOUS TRANSACTIONS
JOURNAL 04/03/23 M ANNUAL INACTIVITY FEE $50.00
Total Miscellaneous Transactions $50.00
"""


def test_apex_statement_parses_and_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="tradier",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2022.1",
        file_name="synthetic.pdf",
    )
    process_batch(batch, STATEMENT_TEXT)
    batch.refresh_from_db()

    assert batch.lines.count() == 6
    assert batch.transaction_count == 6
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    # Balances are carried on every line for harness reconciliation.
    balances = batch.lines.first().raw_data["balances"]
    assert balances == {"opening": "950.00", "closing": "79.14"}

    # Cash: −902.86 + 201.00 + 19.21 + 4050 − 500 − 50 = 2817.35
    assert Holding.current(accounts["cash"], usd) == D("2817.35")

    # The security resolved by CUSIP with the holdings-table ticker.
    sgov = Instrument.objects.get(code="SGOV")
    assert sgov.identifiers.filter(type="cusip", value="46436E718").exists()
    assert Holding.current(accounts["holdings"], sgov) == D("7")  # 9 − 2
