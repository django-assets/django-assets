"""B4: process_batch orchestrator (ADR-0027) + Schwab end-to-end."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportLine
from django_assets.core.models import Transaction

from .conftest import SCHWAB_CSV

pytestmark = pytest.mark.ledger

D = Decimal


def test_lines_persist_before_materialization(batch, aapl, monkeypatch):
    """Spec invariant 5: raw evidence lands first; a crash between parse
    and materialize leaves lines re-processable."""
    from django_assets.brokerage.schemas.builtin.schwab import SchwabTradesCsv2026

    def boom(self, line):
        raise RuntimeError("crash injection")

    monkeypatch.setattr(SchwabTradesCsv2026, "materialize_line", boom)
    with pytest.raises(RuntimeError, match="crash injection"):
        process_batch(batch, SCHWAB_CSV)
    assert batch.lines.count() == 3  # evidence persisted
    assert Transaction.objects.count() == 0

    monkeypatch.undo()
    process_batch(batch, SCHWAB_CSV)  # re-processing completes
    assert batch.lines.count() == 3  # not duplicated
    assert Transaction.objects.count() == 2


def test_schwab_end_to_end(batch, accounts, usd, aapl):
    """Fixture CSV → lines → transactions → matched legs."""
    process_batch(batch, SCHWAB_CSV)
    batch.refresh_from_db()

    assert batch.lines.count() == 3
    buy_line, sell_line, journal_line = batch.lines.order_by("line_number")
    assert buy_line.kind == "broker_trade"
    assert journal_line.kind == "balance_note"  # informational, skipped
    assert batch.transaction_count == 2

    txs = Transaction.objects.order_by("timestamp")
    assert all(tx.origin == "import" for tx in txs)
    buy_tx = txs[0]
    # Source-shape fidelity: the broker's own net amount, not qty×price.
    cash_leg = buy_tx.legs.get(account=accounts["cash"], instrument=usd)
    assert cash_leg.amount == D("-1755.55")

    # Self-reconciliation (ADR-0024 Path 1): eligible legs land in
    # matched_legs — cash + holdings have reconciling profiles.
    matched_accounts = {leg.account.name for leg in buy_line.matched_legs.all()}
    assert matched_accounts == {"brokerage_cash", "brokerage_holdings"}
    assert journal_line.matched_legs.count() == 0


def test_profile_less_accounts_never_match(batch, accounts, usd, aapl):
    """D-10: tracking/external accounts without reconciling profiles stay
    out of matched_legs even though their legs exist."""
    process_batch(batch, SCHWAB_CSV)
    for line in batch.lines.all():
        for leg in line.matched_legs.select_related("account"):
            assert leg.account.name in {"brokerage_cash", "brokerage_holdings"}


def test_invariant_no_eligible_leg_outside_matched(batch, accounts, usd, aapl):
    """ADR-0029 invariant: every eligible leg of an import-origin
    Transaction is in matched_legs after process_batch."""
    process_batch(batch, SCHWAB_CSV)
    matched_ids = set(ImportLine.objects.filter(batch=batch).values_list("matched_legs", flat=True))
    for tx in Transaction.objects.filter(origin="import"):
        for leg in tx.legs.select_related("account"):
            if leg.account.name in {"brokerage_cash", "brokerage_holdings"}:
                assert leg.pk in matched_ids
