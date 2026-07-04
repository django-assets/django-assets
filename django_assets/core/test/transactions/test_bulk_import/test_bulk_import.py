"""C6: TransactionBuilder.bulk_import — spec §4.2, ADR-0019.

Batched insertion of transaction dicts: one DB transaction per batch,
bulk_create for both tables, deferred trigger validating every
Transaction at COMMIT. Batch-agnostic — no ImportBatch coupling here.
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.builder import BulkImportResult, TransactionBuilder
from django_assets.core.exceptions import UnbalancedTransactionError
from django_assets.core.models import Transaction, TransactionLeg

pytestmark = pytest.mark.ledger

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


def make_row(accounts, usd, amount="10.00", **overrides):
    row = {
        "timestamp": TS,
        "account": accounts["cash"],
        "legs": [
            {"account": accounts["cash"], "instrument": usd, "amount": amount},
            {"account": accounts["external"], "instrument": usd, "amount": f"-{amount}"},
        ],
    }
    row.update(overrides)
    return row


def test_thousand_rows_in_batches(accounts, usd):
    rows = [make_row(accounts, usd, description=f"row {i}") for i in range(1000)]
    result = TransactionBuilder.bulk_import(rows, batch_size=100)
    assert isinstance(result, BulkImportResult)
    assert result.inserted == 1000
    assert result.failed == 0
    assert result.errors == []
    assert Transaction.objects.count() == 1000
    assert TransactionLeg.objects.count() == 2000


def test_default_origin_is_manual_and_rows_may_override(accounts, usd):
    """[D-6]: rows may set origin; the default stays 'manual'."""
    rows = [
        make_row(accounts, usd),
        make_row(accounts, usd, origin="import:schwab-csv"),
    ]
    TransactionBuilder.bulk_import(rows)
    origins = sorted(Transaction.objects.values_list("origin", flat=True))
    assert origins == ["import:schwab-csv", "manual"]


def test_optional_fields_round_trip(accounts, usd):
    trade_ts = TS - datetime.timedelta(days=1)
    rows = [
        make_row(
            accounts,
            usd,
            trade_timestamp=trade_ts,
            description="wire in",
            metadata={"ref": "abc-123"},
        )
    ]
    TransactionBuilder.bulk_import(rows)
    tx = Transaction.objects.get()
    assert tx.trade_timestamp == trade_ts
    assert tx.description == "wire in"
    assert tx.metadata == {"ref": "abc-123"}


def test_raise_mode_stops_at_first_error_with_batch_rollback(accounts, usd):
    """ADR-0019: 'raise' stops on the first error; the failing batch rolls
    back while previously committed batches persist."""
    rows = [make_row(accounts, usd) for _ in range(8)]
    rows[5]["legs"][1]["amount"] = "-9.99"  # unbalanced
    with pytest.raises(UnbalancedTransactionError):
        TransactionBuilder.bulk_import(rows, batch_size=4)
    # Batch 1 (rows 0-3) committed; batch 2 (rows 4-7) rolled back entirely.
    assert Transaction.objects.count() == 4


def test_skip_mode_reports_errors_and_keeps_good_rows(accounts, usd):
    rows = [make_row(accounts, usd, description=f"row {i}") for i in range(10)]
    rows[3]["legs"][1]["amount"] = "-9.99"  # unbalanced
    rows[7]["legs"][0]["amount"] = "1.234"  # excess precision for 2dp USD
    result = TransactionBuilder.bulk_import(rows, batch_size=4, on_error="skip")
    assert result.inserted == 8
    assert result.failed == 2
    assert [e.index for e in result.errors] == [3, 7]
    assert "balanced" in result.errors[0].message
    assert "precision" in result.errors[1].message
    assert Transaction.objects.count() == 8


def test_collect_mode_same_semantics(accounts, usd):
    rows = [make_row(accounts, usd) for _ in range(5)]
    rows[0]["legs"][1]["amount"] = "-1.00"  # unbalanced
    result = TransactionBuilder.bulk_import(rows, on_error="collect")
    assert result.inserted == 4
    assert result.failed == 1
    assert result.errors[0].index == 0
    assert Transaction.objects.count() == 4


def test_float_amount_rejected_per_row(accounts, usd):
    rows = [make_row(accounts, usd)]
    rows[0]["legs"][0]["amount"] = 10.0  # float-ok
    result = TransactionBuilder.bulk_import(rows, on_error="collect")
    assert result.failed == 1
    assert "Decimal" in result.errors[0].message
    assert Transaction.objects.count() == 0
