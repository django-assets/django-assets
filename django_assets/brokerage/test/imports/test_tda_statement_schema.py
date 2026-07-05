"""Synthetic coverage for the TD Ameritrade statement-PDF schema
(fabricated statement text; the real corpus is private and
git-excluded)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

STATEMENT_TEXT = """\
Cash Activity Summary Income & Expense Summary Performance Summary
Current YTD
Opening Balance $ 100.00 $ - Income Cost Basis As Of - 01/31/23** $ -
Closing Balance $ 0.00 $0.00 Net $0.00 $0.00 $0.00
page 1 of 4
Statement for Account # 000-000000
01/01/23 - 01/31/23
Account Activity
Trade Settle Acct Transaction/ Symbol/
Date Date Type Cash Activity* Description CUSIP Quantity Price Amount Balance
Opening Balance $ 100.00
01/03/23 01/04/23 Cash - Funds Deposited ELECTRONIC FUNDING - - $ 0.00 $ 1,000.00 1,100.00
01/05/23 01/05/23 Margin Received - CENTRAL PUERTO SA CEPU 100 $ 0.00 $ - 1,100.00
ADR SPONSORED
N:TRANSFER FROM 000000000-2
01/09/23 01/11/23 Margin Buy - Securities Purchased ECOPETROL SA EC 200 2.0000 (401.00) 699.00
Commission/Fee 1.00
01/10/23 01/11/23 Margin Sell - Securities Sold CAMECO CORP - 1- 0.65 64.34 763.34
CCJ Feb 17 23 28.0 C TO OPEN
Commission/Fee 0.65
Regulatory Fee 0.01
01/12/23 01/13/23 Margin Sell - Securities Sold ECOPETROL SA EC 50- 2.1000 104.90 868.24
Regulatory Fee 0.10
01/17/23 01/17/23 Margin Div/Int - Income ISHARES TRUST SGOV - $ 0.00 $ 288.23 1,156.47
01/17/23 01/17/23 Margin Div/Int - Expense PAMPA ENERGIA S.A. PAM - 0.00 (1.72) 1,154.75
ADR FEE
01/18/23 01/18/23 Margin Journal - Other FOREIGN WITHHOLDING - - 0.00 (4.75) 1,150.00
01/19/23 01/19/23 Margin Journal - Other MARK TO MARKET ADJ - - 0.00 - 1,150.00
01/20/23 01/20/23 Margin Ck# - Funds Disbursed Billpay 4BY9TEZ9 CITIBANK - - - $ 0.00 $ (200.00) 950.00
01/25/23 01/25/23 Margin Div/Int - Income Cash Interest from 1/1-1/24 - - $ 0.00 $ 0.03 950.03
01/31/23 01/31/23 Margin Journal - Other PURCHASE FDIC INSURED - - 0.00 (950.03) 0.00
DEPOSIT ACCOUNT
Closing Balance $ 0.00
*For Cash Activity totals, refer to the Cash Activity Summary on page one of your statement.
"""


def test_tda_statement_parses_and_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="tdameritrade",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2012.1",
        file_name="synthetic.pdf",
    )
    process_batch(batch, STATEMENT_TEXT)
    batch.refresh_from_db()

    assert batch.lines.count() == 12
    # MARK TO MARKET ADJ (0 cash, 0 qty) is evidence only, not matchable.
    notes = batch.lines.filter(kind__startswith="note_")
    assert notes.count() == 1
    assert "journal_other" in notes.first().kind
    assert batch.transaction_count == 11
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    # Balances land on the batch (activity section, not the page-one
    # summary and never the Insured Deposit section).
    assert batch.metadata["balances"] == {"opening": "100.00", "closing": "0.00"}
    assert batch.metadata["recognized"] is True

    # Cash moves by exactly closing − opening.
    assert Holding.current(accounts["cash"], usd) == D("-100.00")

    # Equity bought 200, sold 50; identity by ticker from the symbol column.
    ec = Instrument.objects.get(code="EC")
    assert Holding.current(accounts["holdings"], ec) == D("150")

    # Received shares arrive as a quantity adjustment.
    cepu = Instrument.objects.get(code="CEPU")
    assert Holding.current(accounts["holdings"], cepu) == D("100")

    # The option row: identity from the continuation line, short 1 contract,
    # net premium 64.34 = 1×0.65×100 − 0.66 fees.
    option = Instrument.objects.get(code="CCJ 02/17/2023 28 C")
    assert Holding.current(accounts["holdings"], option) == D("-1")

    # Buy principal recovered from the NET amount: 401.00 − 1.00 fee.
    buy_line = batch.lines.get(line_number=3)
    assert buy_line.raw_data["fee"] == "1.00"
    assert buy_line.raw_data["amount"] == "(401.00)"


def test_tda_inactive_month_exposes_summary_balances(accounts, usd):
    text = (
        "Cash Activity Summary Income & Expense Summary Performance Summary\n"
        "Opening Balance $ 0.00 $ - Income Cost Basis As Of - 02/28/23** $ -\n"
        "Closing Balance $ 0.00 $0.00 Net $0.00 $0.00 $0.00\n"
        "Insured Deposit Account Activity\n"
        "Opening Balance $9,266.18\n"
        "Closing Balance $9,266.26\n"
    )
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="tdameritrade",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2012.1",
        file_name="inactive.pdf",
    )
    process_batch(batch, text)
    batch.refresh_from_db()
    assert batch.lines.count() == 0
    # Page-one summary wins; the Insured Deposit balances are ignored.
    assert batch.metadata["balances"] == {"opening": "0.00", "closing": "0.00"}


def test_tda_scan_without_text_layer_is_flagged(accounts):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="tdameritrade",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2012.1",
        file_name="scan.pdf",
    )
    process_batch(batch, "OCR gibberish with no landmarks at all")
    batch.refresh_from_db()
    assert batch.lines.count() == 0
    assert batch.metadata["recognized"] is False
