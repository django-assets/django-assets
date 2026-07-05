"""Synthetic coverage for the Robinhood statement-PDF schema (fabricated
statement text — the plain-text path uses per-code direction defaults;
the real corpus exercises the <DR>/<CR> positional markers)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

STATEMENT_TEXT = """\
Account Summary Opening Balance Closing Balance
Net Account Balance $100.00 $1,983.55
Portfolio Value $100.00 $5,000.00
Account Activity
Description Symbol Acct Type Transaction Date Qty Price Debit Credit
ACH Deposit Margin ACH 12/11/2020 $2,000.00
Catalyst Pharmaceuticals
Margin REC 12/14/2020 1
Unsolicited, CUSIP: 14888U101
PSTH Margin Buy 12/15/2020 10 $28.85 $288.50
DLB 02/19/2021 Call $90.00 DLB Margin BTO 12/23/2020 1 $7.47 $747.00
DLB 02/19/2021 Call $95.00 DLB Margin STO 12/23/2020 1 $4.60 $459.97
Cash Div: R/D 2020-12-20 P/D 2020-12-28 - 9 shares at 1.2 PM Margin CDIV 12/28/2020 $10.80
Stock Lending SGOV Margin SLIP 12/29/2020 $0.01
Gold Fee Margin GOLD 12/30/2020 $5.00
DLB 01/15/2021 Call $80.00 Margin OEXP 12/31/2020 1
ACAT IN control_num = 1, firm_id = 0164, acct_num = 1 Cash ACATI 12/31/2020 $8,000.00
AT&T Cash ACATI 12/31/2020 3
CUSIP: 00206R102
Interest Payment Sweep INT 12/31/2020 $6.09
Instant bank transfer withdrawal - account ending in 5117 Margin RTP 12/31/2020 $7,552.82
Total Funds Paid and Received $1,495.00 $2,459.97
"""


def test_robinhood_statement_parses_and_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="robinhood",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2020.1",
        file_name="2020-12.pdf",
    )
    process_batch(batch, STATEMENT_TEXT)
    batch.refresh_from_db()

    assert batch.metadata["balances"] == {"opening": "100.00", "closing": "1983.55"}
    assert batch.lines.count() == 13
    assert batch.transaction_count == 13
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    # +2000 −288.50 −747 +459.97 +10.80 +0.01 −5 +8000 +6.09 −7552.82
    # = +1,883.55 == closing − opening.
    assert Holding.current(accounts["cash"], usd) == D("1883.55")

    psth = Instrument.objects.get(code="PSTH")
    assert Holding.current(accounts["holdings"], psth) == D("10")

    # REC shares arrive with identity from the CUSIP continuation and the
    # description from the preceding wrapped line.
    rec_line = batch.lines.get(kind="broker_rec")
    assert rec_line.raw_data["cusip"] == "14888U101"
    assert "Catalyst" in rec_line.raw_data["description"]

    # Long and short option legs from the descriptor.
    long_call = Instrument.objects.get(code="DLB 02/19/2021 90 C")
    short_call = Instrument.objects.get(code="DLB 02/19/2021 95 C")
    assert Holding.current(accounts["holdings"], long_call) == D("1")
    assert Holding.current(accounts["holdings"], short_call) == D("-1")

    # ACAT stock row: quantity only, by CUSIP.
    att = batch.lines.filter(kind="broker_acati").exclude(raw_data__cusip="").count()
    assert att >= 1


def test_robinhood_statement_first_month_has_no_opening(accounts):
    text = (
        "Account Summary Opening Balance Closing Balance\n"
        "Net Account Balance N/A $2,000.00\n"
        "Account Activity\n"
        "ACH Deposit Margin ACH 04/29/2025 $2,000.00\n"
        "Total Funds Paid and Received $2,000.00 $0.00\n"
    )
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="robinhood",
        schema_document_kind="statement",
        schema_format_kind="pdf",
        schema_version="2020.1",
        file_name="2025-04.pdf",
    )
    process_batch(batch, text)
    batch.refresh_from_db()
    assert batch.metadata["balances"] == {"closing": "2000.00"}
    assert batch.transaction_count == 1
