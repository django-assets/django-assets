"""Synthetic coverage for the Schwab statement-PDF schema (fabricated
kerning-collapsed statement text; the real corpus is private)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

STATEMENT_TEXT = """\
Transactions - Summary
BeginningCash*asof11/01 + Deposits + Withdrawals + Purchases + Sales/Redemptions + Dividends/Interest + Expenses = EndingCash*asof11/30
$100.00 $2,000.00 ($500.00) ($1,030.62) $26,018.62 $3.81 ($8.48) $26,583.33
OtherActivity ($75.00) Otheractivityincludestransactionswhichdon'taffectthecashbalance.
Transaction Details
Symbol/ Price/Rate Charges/ Realized
Date Category Action CUSIP Description Quantity perShare($) Interest($) Amount($) Gain/(Loss)($)
11/04 Deposit JournaledFunds IRAROLLOVERCONTRIB 2,000.00
11/05 Withdrawal MoneyLinkTxn TOBANKACCT (500.00)
11/06 Purchase BRKB BERKSHIREHATHAWAYCLASS 2.0000 515.3100 (1,030.62)
11/12 Sale MSTR CALLMICROSTRATEGYINC $300 (1.0000) 260.2000 1.38 26,018.62 981.96,(LT)
01/16/2026 EXP01/16/26
300.00C Commission$0.65;ExchangeProcessingFee$0.73
11/15 Dividend CashDividend QQQ INVSCQQQTRUSTSRS1 3.81
11/29 Expense MarginInterest INTEREST10/28THRU11/28 (8.48)
11/30 Other AccountTransfer IAU ISHARESGOLDETF 72.0000 44.2000 3,182.40
Other ExpiredLong GOOGL PUTALPHABETINC $142 1.0000
12/13/2024 EXP12/13/24
142.00P
TotalTransactions $24,483.33 $0.00
"""


def test_schwab_statement_parses_and_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2024.1",
        file_name="Brokerage Statement_2024-11-30_534.PDF",
    )
    process_batch(batch, STATEMENT_TEXT)
    batch.refresh_from_db()

    assert batch.metadata["balances"] == {"opening": "100.00", "closing": "26583.33"}
    assert batch.lines.count() == 8
    assert batch.transaction_count == 8
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    # Cash: +2000 −500 −1030.62 +26018.62 +3.81 −8.48 = 26,483.33
    # ("Other" rows move no cash — the 3,182.40 is a market value).
    assert Holding.current(accounts["cash"], usd) == D("26483.33")

    brkb = Instrument.objects.get(code="BRKB")
    assert Holding.current(accounts["holdings"], brkb) == D("2")

    # Option sold short: identity from row strike + continuation expiry/right.
    mstr_call = Instrument.objects.get(code="MSTR 01/16/2026 300 C")
    assert Holding.current(accounts["holdings"], mstr_call) == D("-1")

    # Transferred-in shares arrive as a quantity adjustment (no cash).
    iau = Instrument.objects.get(code="IAU")
    assert Holding.current(accounts["holdings"], iau) == D("72")

    # ExpiredLong: no prior position in this synthetic, so the row-sign
    # fallback applies (real statements sit on top of the opening trade).
    googl_put = Instrument.objects.get(code="GOOGL 12/13/2024 142 P")
    assert Holding.current(accounts["holdings"], googl_put) == D("-1")


def test_schwab_statement_scan_flagged(accounts):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2024.1",
        file_name="Brokerage Statement_2024-12-31_000.PDF",
    )
    process_batch(batch, "no landmarks here")
    batch.refresh_from_db()
    assert batch.lines.count() == 0
    assert batch.metadata["recognized"] is False
