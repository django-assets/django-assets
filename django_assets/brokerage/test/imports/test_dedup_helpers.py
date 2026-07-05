"""B4: host-driven pre-flight dedup helpers (ADR-0019 §5.4) — the three
worked examples as integration tests, plus period replacement."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.dedup import (
    delete_import_batch,
    find_by_external_ids,
    get_imported_periods,
    is_file_imported,
    is_period_imported,
)
from django_assets.brokerage.imports import import_transactions, process_batch
from django_assets.brokerage.models import ImportBatch, TransactionImport
from django_assets.core.models import Transaction

from .conftest import SCHWAB_CSV, TS

pytestmark = pytest.mark.ledger

D = Decimal
MARCH = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))


def test_period_discipline(batch, accounts):
    """Worked example 1: skip a statement whose period is already in."""
    assert is_period_imported(accounts["cash"], "schwab", "trades", *MARCH)
    assert not is_period_imported(
        accounts["cash"],
        "schwab",
        "trades",
        datetime.date(2026, 4, 1),
        datetime.date(2026, 4, 30),
    )
    # Keyed on (account, broker, document_kind); format/version excluded.
    assert not is_period_imported(accounts["cash"], "fidelity", "trades", *MARCH)
    assert get_imported_periods(accounts["cash"], "schwab", "trades") == [MARCH]


def test_period_replacement(batch, accounts, usd, aapl):
    """Worked example 2: delete the old batch (cascade incl. transactions,
    trigger passes on whole-transaction deletes), re-import."""
    process_batch(batch, SCHWAB_CSV)
    assert Transaction.objects.count() == 2
    deleted = delete_import_batch(batch)
    assert deleted["transactions"] == 2
    assert Transaction.objects.count() == 0
    assert not ImportBatch.objects.filter(pk=batch.pk).exists()


def test_external_id_filtering(batch, accounts, usd):
    """Worked example 3: stable-ID dedup pre-flight."""
    rows = [
        {
            "timestamp": TS,
            "account": accounts["cash"],
            "legs": [
                {"account": accounts["cash"], "instrument": usd, "amount": "10.00"},
                {"account": accounts["market"], "instrument": usd, "amount": "-10.00"},
            ],
            "_import_external_id": f"exec-{i}",
        }
        for i in range(3)
    ]
    result = import_transactions(rows, batch=batch)
    assert result.inserted == 3
    seen = find_by_external_ids(
        accounts["cash"], "schwab", "trades", ["exec-0", "exec-2", "exec-9"]
    )
    assert seen == {"exec-0", "exec-2"}


def test_file_hash_short_circuit(batch, accounts):
    assert is_file_imported(accounts["cash"], "abc123")
    assert not is_file_imported(accounts["cash"], "deadbeef")


def test_import_transactions_links_and_uniqueness(batch, accounts, usd):
    rows = [
        {
            "timestamp": TS,
            "account": accounts["cash"],
            "legs": [
                {"account": accounts["cash"], "instrument": usd, "amount": "5.00"},
                {"account": accounts["market"], "instrument": usd, "amount": "-5.00"},
            ],
            "_import_external_id": "exec-1",
            "_import_source_data": {"row": 1},
        },
        {
            "timestamp": TS,
            "account": accounts["cash"],
            "legs": [
                {"account": accounts["cash"], "instrument": usd, "amount": "6.00"},
                {"account": accounts["market"], "instrument": usd, "amount": "-6.00"},
            ],
            # no external id: blank allowed, still linked to the batch
        },
    ]
    result = import_transactions(rows, batch=batch)
    assert result.inserted == 2
    imports = TransactionImport.objects.filter(batch=batch).order_by("pk")
    assert imports.count() == 2
    assert imports[0].external_id == "exec-1"
    assert imports[0].source_data == {"row": 1}
    assert imports[1].external_id == ""

    # Unique per batch when non-blank.
    from django.db import IntegrityError
    from django.db import transaction as db_tx

    with pytest.raises(IntegrityError), db_tx.atomic():
        TransactionImport.objects.create(
            transaction=imports[1].transaction, batch=batch, external_id="exec-1"
        )
