"""Synthetic coverage for the real-format Schwab transactions schema
(every action the 2023–2025 exports contain, none of the private data)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Transaction
from django_assets.core.queries import Holding

from .conftest import TS  # noqa: F401

pytestmark = pytest.mark.ledger

D = Decimal

HEADER = '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'

# Newest-first, like the real export. Chronology (oldest → newest):
# deposit → buy 100 AAPL → sell 40 → sell short 10 SPXS → STO 2 calls →
# 1 assigned, 1 expired → BTO 1 put → exercise it → dividends/interest/
# fees → 2:1 split on the remaining 60 AAPL → reverse-split pair (OLD →
# NEW) → journaled shares out → moneylink out.
ROWS = [
    '"12/20/2024","MoneyLink Transfer","","Tfr OUT","","","","-$500.00"',
    '"12/19/2024","Journaled Shares","AAPL","TDA TO CS","-20","$100.00","",""',
    '"12/18/2024","Reverse Split","NEWCO","REV SPLIT NEW","5","","",""',
    '"12/18/2024","Reverse Split","OLDCO","REV SPLIT OLD","-50","","",""',
    '"12/17/2024","Security Transfer","OLDCO","XFER IN","50","","",""',
    '"12/16/2024","Stock Split","AAPL","2 FOR 1","60","","",""',
    '"12/15/2024","Foreign Tax Paid","EC","NR TAX","","","","-$30.00"',
    '"12/14/2024","ADR Mgmt Fee","EC","ADR FEE","","","","-$5.00"',
    '"12/13/2024","Margin Interest","","INTEREST","","","","-$12.00"',
    '"12/12/2024","Bank Interest","","BANK INT","","","","$3.50"',
    '"12/11/2024","Non-Qualified Div Adj","SQQQ","RECLASS","","","","-$7.00"',
    '"12/10/2024","Qualified Dividend","EC","DIV","","","","$52.00"',
    '"12/09/2024","Cash In Lieu","AAPL","CIL","","","","$18.25"',
    '"12/08/2024 as of 12/07/2024","Cash Dividend","AAPL","DIV","","","","$24.00"',
    '"12/07/2024","Exchange or Exercise","XYZ 12/20/2024 50.00 P","EXERCISE","-1","","",""',
    '"12/06/2024","Buy to Open","XYZ 12/20/2024 50.00 P","PUT","1","$2.00","$0.66","-$200.66"',
    '"12/05/2024","Expired","AAPL 12/06/2024 250.00 C","EXPIRED","1","","",""',
    '"12/04/2024","Assigned","AAPL 12/06/2024 240.00 C","ASSIGNED","1","","",""',
    '"12/03/2024","Sell to Open","AAPL 12/06/2024 240.00 C","CALL","1","$3.00","$0.66","$299.34"',
    '"12/03/2024","Sell to Open","AAPL 12/06/2024 250.00 C","CALL","1","$1.00","$0.66","$99.34"',
    '"12/02/2024","Sell Short","SPXS","BEAR","10","$6.00","$0.10","$59.90"',
    '"12/01/2024","Sell","AAPL","SELL","40","$110.00","$0.08","$4399.92"',
    '"11/30/2024","Buy","AAPL","BUY","100","$100.00","","-$10000.00"',
    '"11/29/2024","MoneyLink Transfer","","Tfr IN","","","","$25000.00"',
]

CSV = "\n".join([HEADER, *ROWS, ""])


@pytest.fixture
def batch(accounts):
    return ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="transactions",
        schema_format_kind="csv",
        schema_version="2024.1",
        file_name="synthetic.csv",
    )


def test_every_action_materializes_and_cash_reconciles(batch, accounts, usd, aapl):
    from django_assets.brokerage.schemas.instruments import parse_money

    process_batch(batch, CSV)
    batch.refresh_from_db()

    # The reverse-split pair merged into one conversion line.
    assert batch.lines.filter(kind="broker_conversion").count() == 1
    assert batch.lines.count() == len(ROWS) - 1
    assert batch.transaction_count == len(ROWS) - 1
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    expected = sum((parse_money(row.split('","')[-1].rstrip('"')) for row in ROWS), D(0))
    cash = Holding.current(accounts["cash"], _usd())
    assert cash == expected

    # Positions: AAPL 100−40 → split ×2 → −20 journaled = 100.
    aapl_instrument = _instrument("AAPL")
    assert Holding.current(accounts["holdings"], aapl_instrument) == D("100")
    # Short SPXS stays open at −10; options all closed.
    assert Holding.current(accounts["holdings"], _instrument("SPXS")) == D("-10")
    # Instrument codes canonicalize the strike (240.00 → 240).
    for code in ("AAPL 12/06/2024 240 C", "AAPL 12/06/2024 250 C", "XYZ 12/20/2024 50 P"):
        assert Holding.current(accounts["holdings"], _instrument(code)) == D("0")
    # Reverse-split conversion: OLDCO gone, NEWCO present, tag written.
    assert Holding.current(accounts["holdings"], _instrument("OLDCO")) == D("0")
    assert Holding.current(accounts["holdings"], _instrument("NEWCO")) == D("5")
    conversion = Transaction.objects.filter(metadata__has_key="conversion").get()
    assert conversion.metadata["conversion"]["from_quantity"] == "50"

    # The tagged 2:1 split carries its ratio for lots.
    split = Transaction.objects.filter(metadata__has_key="corporate_action").get()
    assert split.metadata["corporate_action"]["ratio"] == "2"

    # "as of" date became the trade timestamp.
    dividend = Transaction.objects.filter(description__startswith="Cash Dividend AAPL").get()
    assert dividend.trade_timestamp is not None
    assert dividend.trade_timestamp.date() == datetime.date(2024, 12, 7)

    # Option instruments were created with metadata.
    from django_assets.instruments.options.models import OptionMeta

    assert OptionMeta.objects.count() == 3


def test_option_removal_directions(batch, accounts, usd, aapl):
    """Assigned/Expired close shorts; Exchange or Exercise closes longs —
    all by position sign, proven by the flat option book above."""
    process_batch(batch, CSV)
    from django_assets.lots.models import LotMatch
    from django_assets.lots.rebuild import rebuild_lots

    rebuild_lots(accounts["holdings"])  # conservation trigger passes
    assert LotMatch.objects.filter(lot__account=accounts["holdings"]).exists()


def _usd():
    from django_assets.core.models import Instrument

    return Instrument.objects.get(code="USD")


def _instrument(code: str):
    from django_assets.core.models import Instrument

    return Instrument.objects.get(code=code)
