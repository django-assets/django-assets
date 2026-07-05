"""Synthetic coverage for the Robinhood activity schema (every trans
code the 2020–2024 exports contain, none of the private data)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.core.models import Instrument, Transaction
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

HEADER = (
    '"Activity Date","Process Date","Settle Date","Instrument","Description",'
    '"Trans Code","Quantity","Price","Amount"'
)

# Newest-first like the real export; note the multi-line quoted
# description (Buy) and the disclaimer footer rows.
ROWS = [
    '"12/20/2024","12/20/2024","12/20/2024","","Instant bank transfer","RTP","","","($100.00)"',
    '"12/19/2024","12/19/2024","12/19/2024","VTI","CIL on 0.5 @ $10 - VTI","CIL","","","$5.00"',
    '"12/18/2024","12/18/2024","12/18/2024","VTI","Vanguard\nCUSIP: 922908769","SPL","100","",""',
    '"12/17/2024","12/17/2024","12/17/2024","GME","GameStop\nCUSIP: 36467W109","REC","3","",""',
    '"12/16/2024","12/16/2024","12/16/2024","VALE","Foreign Tax Witholding at $2.00","DTAX","","","($2.00)"',
    '"12/15/2024","12/15/2024","12/15/2024","VALE","Sponsored ADR fee","DFEE","","","($0.50)"',
    '"12/14/2024","12/14/2024","12/14/2024","","Aggregated Margin Interest","MINT","","","($1.20)"',
    '"12/13/2024","12/13/2024","12/13/2024","","Gold Fee","GOLD","","","($5.00)"',
    '"12/12/2024","12/12/2024","12/12/2024","QQQ","Stock Lending","SLIP","","","$0.75"',
    '"12/11/2024","12/11/2024","12/11/2024","QQQ","Manufactured Div","MDIV","","","$1.10"',
    '"12/10/2024","12/10/2024","12/10/2024","VTI","Cash Div","CDIV","","","$12.00"',
    '"12/09/2024","12/09/2024","12/09/2024","PSTH","Option Expiration for PSTH 12/06/2024 Call $30.00","OEXP","2","",""',
    '"12/05/2024","12/05/2024","12/06/2024","PSTH","PSTH 12/06/2024 Call $30.00","BTO","2","$1.00","($200.00)"',
    '"12/04/2024","12/04/2024","12/05/2024","DLB","DLB 12/20/2024 Call $90.00","BTC","1","$0.50","($50.02)"',
    '"12/03/2024","12/03/2024","12/04/2024","DLB","DLB 12/20/2024 Call $90.00","STO","1","$4.60","$459.97"',
    '"12/02/2024","12/02/2024","12/03/2024","VTI","Sale","Sell","20","$110.00","$2,199.95"',
    '"12/01/2024","12/01/2024","12/02/2024","VTI","Vanguard Total Market\nCUSIP: 922908769","Buy","100.5","$100.00","($10,050.00)"',
    '"11/30/2024","11/30/2024","11/30/2024","","ACH Deposit","ACH","","","$20,000.00"',
    "",
    '"","","","","Disclaimer footer text","","","",""',
]

CSV = "\n".join([HEADER, *ROWS])


def test_every_code_materializes_and_cash_reconciles(accounts, usd):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="robinhood",
        schema_document_kind="activity",
        schema_format_kind="csv",
        schema_version="2020.1",
        file_name="synthetic.csv",
    )
    process_batch(batch, CSV)
    batch.refresh_from_db()

    assert batch.lines.count() == 18  # footer + blank skipped
    assert batch.transaction_count == 18
    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    expected = (
        D("20000.00")
        - D("10050.00")
        + D("2199.95")
        + D("459.97")
        - D("50.02")
        - D("200.00")
        + D("12.00")
        + D("1.10")
        + D("0.75")
        - D("5.00")
        - D("1.20")
        - D("0.50")
        - D("2.00")
        + D("5.00")
        - D("100.00")
    )
    assert Holding.current(accounts["cash"], usd) == expected

    vti = Instrument.objects.get(code="VTI")
    # 100.5 bought − 20 sold = 80.5, then the SPL adds 100 (untaggable
    # ratio 180.5/80.5 is fine — it IS exact) → check final position.
    assert Holding.current(accounts["holdings"], vti) == D("180.5")
    assert Holding.current(accounts["holdings"], Instrument.objects.get(code="GME")) == D("3")
    # Options: DLB short opened and closed; PSTH long expired.
    assert Holding.current(
        accounts["holdings"], Instrument.objects.get(code="DLB 12/20/2024 90 C")
    ) == D("0")
    assert Holding.current(
        accounts["holdings"], Instrument.objects.get(code="PSTH 12/06/2024 30 C")
    ) == D("0")
    # Fee/tax trackers accumulated.
    assert Holding.current(accounts["foreign_tax"], usd) == D("2.00")
    assert Holding.current(accounts["margin_interest"], usd) == D("1.20")
    assert Transaction.objects.filter(origin="import").count() == 18
