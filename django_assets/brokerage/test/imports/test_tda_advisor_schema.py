"""Synthetic coverage for the TDA advisor-managed statement schema
(fabricated Invest-In-Vol-style text; the real corpus is private)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

STATEMENT_TEXT = """\
MONTHLY STATEMENT Reporting Period: April1 - 30, 2023 ROLLOVER IRA
ACCOUNT SUMMARY Total Account Value: $54,389.46
CASH AND CASH ALTERNATIVES
TOTAL CASH & CASH ALTERNATIVES $30,163.99
TRANSACTIONS DETAIL
Transaction Settlement Symbol/ Transaction
Date Date Activity Type Description CUSIP Quantity Price Amount
04/13 04/13 Deposits to Account TRANSFER 944772250-2 TO 947365774-2 - - $ - $53,032.40
04/13 04/17 Buy VS TRUST SVIX 895 17.8999 (16,020.41)
-1X SHORT VIX FUTURES ETF
04/17 04/18 Buy PROSHARES TRUST II - 25 0.03 (91.50)
UVXY APR 28 23 6.5 C TO OPEN
04/17 04/19 Sell VS TRUST SVIX (395) 17.8501 7,050.79
-1X SHORT VIX FUTURES ETF
04/28 04/28 Dividends and Interest FDIC INSURED DEPOSIT ACCOUNT MMDA12 - - 1.50
05/01 05/02 Buy ISHARES TRUST SGOV 44 100.61 (4,426.84)
ISHARES 0-3 MONTH TREASURY
04/28 04/28 Other Income or Expense 04/10/2023-04/28/2023 MGMT FEE - - - (370.61)
INVEST IN VOL LLC 9473657742
04/03 04/03 Deliver PROSHARES TRUST II - (12) - -
UVXY APR 28 23 6.0 C
EXPIRATION
INSURED DEPOSIT ACCOUNT ACTIVITY
Transaction Settlement
Date Date Transaction Description Amount Balance
Opening Balance -
04/14 04/14 Received FDIC INSURED DEPOSIT ACCOUNT $19,751.89 19,751.89
04/28 04/28 Received INTEREST: INSURED 0.32 19,752.21
TRADES PENDING SETTLEMENT
"""


def test_tda_advisor_statement_parses_and_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="tdameritrade",
        schema_document_kind="advisor-statement",
        schema_format_kind="pdf",
        schema_version="2023.1",
        file_name="947365774 2023-04.pdf",
    )
    process_batch(batch, STATEMENT_TEXT)
    batch.refresh_from_db()

    assert batch.metadata["balances"] == {"closing": "30163.99"}
    assert batch.lines.count() == 10
    # Notes: the out-of-period May-settling buy and the IDA rows — the
    # sweep mirror AND its INTEREST: credit (that income re-prints in
    # TRANSACTIONS DETAIL as the MMDA row).
    assert batch.lines.filter(kind__startswith="note_").count() == 3
    assert batch.lines.filter(kind="note_pending_buy").count() == 1
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    # Cash: +53,032.40 −16,020.41 −91.50 +7,050.79 +1.50 −370.61
    assert Holding.current(accounts["cash"], usd) == D("43602.17")

    svix = Instrument.objects.get(code="SVIX")
    assert Holding.current(accounts["holdings"], svix) == D("500")

    # Option bought via uppercase-month continuation descriptor.
    call = Instrument.objects.get(code="UVXY 04/28/2023 6.5 C")
    assert Holding.current(accounts["holdings"], call) == D("25")

    # Deliver + EXPIRATION removes contracts (row-sign fallback with no
    # prior position in this synthetic; real corpora sit on the open).
    expired = Instrument.objects.get(code="UVXY 04/28/2023 6 C")
    assert Holding.current(accounts["holdings"], expired) == D("-12")
