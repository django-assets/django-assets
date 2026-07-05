"""B7: dedup proposals — the ADR-0029 test spine."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportLine, ImportLineProposal
from django_assets.brokerage.review import (
    confirm_proposal,
    current_proposal,
    materialize_new,
    override_match,
    reject_proposal,
)
from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Transaction

from ..imports.conftest import SCHWAB_CSV
from .conftest import BUY_DATE

pytestmark = pytest.mark.ledger

D = Decimal


def buy_line(batch):
    return batch.lines.get(line_number=1)


def test_central_use_case(batch, accounts, usd, aapl, manual_buy):
    """Manual AAPL entry, then the CSV import: the line waits with a
    proposal instead of materializing a duplicate; confirm links the
    manual's eligible legs and no new Transaction appears."""
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)

    # Not materialized: only the manual + the sell-row transaction exist.
    assert Transaction.objects.filter(origin="import").count() == 1  # the Sell row
    proposal = current_proposal(line)
    assert proposal is not None
    assert proposal.candidate_transaction == manual_buy
    assert proposal.score_total == 0.0  # exact amount, same date
    assert proposal.rank == 1

    confirm_proposal(proposal)
    proposal.refresh_from_db()
    assert proposal.resolution == "confirmed"
    matched = {leg.account.name for leg in line.matched_legs.all()}
    assert "brokerage_cash" in matched
    assert Transaction.objects.filter(origin="import").count() == 1  # still no dup
    assert current_proposal(line) is None


def test_reject_advance_chain(batch, accounts, usd, aapl, manual_buy):
    """Second candidate surfaces on reject; exhausting all falls back to
    materialize-new."""
    with TransactionBuilder(
        account=accounts["cash"],
        timestamp=BUY_DATE + datetime.timedelta(days=1),
        description="another candidate",
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1750.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="1750.00")
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)

    first = current_proposal(line)
    assert first.candidate_transaction == manual_buy  # rank 1: exact match
    remaining = reject_proposal(first)
    assert remaining is not None
    assert remaining.rank == 2

    result = reject_proposal(remaining)
    assert result is None  # exhausted → caller materializes
    materialize_new(line)
    assert line.metadata["materialized"]
    assert line.matched_legs.count() > 0


def test_materialize_new_fast_path(batch, accounts, usd, aapl, manual_buy):
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)
    materialize_new(line)
    assert (
        ImportLineProposal.objects.filter(line=line, resolution="rejected").count()
        == ImportLineProposal.objects.filter(line=line).count()
    )
    # The manual stays; a fresh import-origin duplicate now exists (the
    # user said "these are different events").
    assert Transaction.objects.filter(origin="manual").count() == 1
    assert line.matched_legs.count() > 0


def test_override_creates_audit_row(batch, accounts, usd, aapl, manual_buy):
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)
    override_match(line, manual_buy)
    synthetic = ImportLineProposal.objects.get(line=line, resolution="confirmed")
    assert synthetic.score_breakdown.get("user_override") is True
    assert line.matched_legs.count() > 0


def test_score_threshold_discards_wild_candidates(batch, accounts, usd, aapl):
    """Amount drift caps at 1.0, so a wild amount PLUS days of
    unexplained date drift crosses max_score_to_propose=3.0 — such
    candidates are discarded at scoring time, never stored."""
    with TransactionBuilder(
        account=accounts["cash"],
        timestamp=BUY_DATE + datetime.timedelta(days=6),  # in window, drift 4
        description="unrelated",
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-9.99")
        b.add_leg(account=accounts["market"], instrument=usd, amount="9.99")
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)
    assert current_proposal(line) is None
    assert line.metadata["materialized"]  # went straight through


def test_stale_candidate_auto_supersedes(batch, accounts, usd, aapl, manual_buy):
    """Candidate re-reconciled by another line since scoring → proposal
    superseded on view; surface advances."""
    process_batch(batch, SCHWAB_CSV)
    line = buy_line(batch)
    proposal = ImportLineProposal.objects.get(line=line, rank=1)

    # Another line claims the candidate's cash leg in the meantime.
    other = ImportLine.objects.create(batch=batch, line_number=99, kind="broker_trade", raw_data=[])
    cash_leg = manual_buy.legs.get(account=accounts["cash"])
    other.matched_legs.add(cash_leg)

    assert current_proposal(line) is None  # nothing valid left
    proposal.refresh_from_db()
    assert proposal.resolution == "superseded"
