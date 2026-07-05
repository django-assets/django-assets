"""ADR-0036: checkpoint classification and the approve/reject loop
(synthetic ledgers; the real corpus provides the ZIVB→ZVOL fixture)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.checkpoints import (
    approve_proposal,
    reject_proposal,
    run_checkpoint,
)
from django_assets.brokerage.models import CorporateActionProposal
from django_assets.brokerage.schemas.positions import StatementPosition
from django_assets.core.models import Identifier, Instrument
from django_assets.core.queries import Holding
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta

pytestmark = pytest.mark.ledger

D = Decimal
TS = datetime.datetime(2026, 1, 10, 20, 0, tzinfo=datetime.UTC)
AS_OF = datetime.date(2026, 1, 31)


def make_equity(code, usd):
    inst = Instrument.objects.create(
        code=code, quantity_decimals=8, price_decimals=4, price_currency=usd
    )
    Identifier.objects.create(instrument=inst, type="ticker", value=code)
    EquityMeta.objects.create(instrument=inst)
    return inst


@pytest.fixture
def held_oldco(accounts, usd):
    oldco = make_equity("OLDCO", usd)
    eq.buy_shares(
        accounts=accounts,
        instrument=oldco,
        quantity=200,
        price="20.705",
        principal="4141.00",
        timestamp=TS,
        origin="import",
    )
    return oldco


def test_explained_positions_raise_nothing(accounts, usd, held_oldco):
    proposals = run_checkpoint(
        account=accounts["holdings"],
        positions=[StatementPosition(quantity=D("200"), ticker="OLDCO")],
        as_of=AS_OF,
    )
    assert proposals == []
    assert CorporateActionProposal.objects.count() == 0


def test_rename_detected_and_approval_books_conversion(accounts, usd, held_oldco):
    # The statement now calls the position NEWCO — quantity conserved.
    proposals = run_checkpoint(
        account=accounts["holdings"],
        positions=[StatementPosition(quantity=D("200"), ticker="NEWCO", description="NEWCO ETF")],
        as_of=AS_OF,
        source_reference="synthetic/2026-01",
    )
    assert [p.action_kind for p in proposals] == ["rename"]
    proposal = proposals[0]
    assert proposal.from_instrument == held_oldco
    assert proposal.to_instrument is None  # never traded: unresolved
    assert proposal.evidence["quantity_conserved"] is True

    # Re-running detection never duplicates (fingerprint-stable).
    again = run_checkpoint(
        account=accounts["holdings"],
        positions=[StatementPosition(quantity=D("200"), ticker="NEWCO", description="NEWCO ETF")],
        as_of=AS_OF,
    )
    assert again == []

    # Approval: the user supplies/creates the target deliberately.
    newco = make_equity("NEWCO", usd)
    transaction = approve_proposal(proposal, to_instrument=newco, note="fund rebrand")
    assert Holding.current(accounts["holdings"], held_oldco) == 0
    assert Holding.current(accounts["holdings"], newco) == D("200")
    assert transaction.metadata["corporate_action_proposal"] == proposal.pk
    proposal.refresh_from_db()
    assert proposal.resolution == "approved"
    assert proposal.booked_transaction == transaction

    # Approved interpretation explains the next checkpoint: silence.
    assert (
        run_checkpoint(
            account=accounts["holdings"],
            positions=[StatementPosition(quantity=D("200"), ticker="NEWCO")],
            as_of=datetime.date(2026, 2, 28),
        )
        == []
    )


def test_split_detected_with_simple_ratio(accounts, usd, held_oldco):
    proposals = run_checkpoint(
        account=accounts["holdings"],
        positions=[StatementPosition(quantity=D("400"), ticker="OLDCO")],
        as_of=AS_OF,
    )
    assert [p.action_kind for p in proposals] == ["split"]
    assert proposals[0].evidence["ratio"] == "2"
    transaction = approve_proposal(proposals[0])
    assert Holding.current(accounts["holdings"], held_oldco) == D("400")
    assert transaction.metadata["corporate_action_proposal"] == proposals[0].pk


def test_merger_detected_when_quantities_differ(accounts, usd, held_oldco):
    proposals = run_checkpoint(
        account=accounts["holdings"],
        positions=[StatementPosition(quantity=D("37"), ticker="ACQ", description="ACQUIRER CORP")],
        as_of=AS_OF,
    )
    assert [p.action_kind for p in proposals] == ["merger"]
    acq = make_equity("ACQ", usd)
    approve_proposal(proposals[0], to_instrument=acq)
    assert Holding.current(accounts["holdings"], held_oldco) == 0
    assert Holding.current(accounts["holdings"], acq) == D("37")


def test_unexplained_never_books(accounts, usd, held_oldco):
    # OLDCO vanished while KEEP survives: a real disappearance, not a
    # parser gap (empty parses against a non-empty ledger are silenced).
    keep = make_equity("KEEP", usd)
    eq.buy_shares(
        accounts=accounts,
        instrument=keep,
        quantity=5,
        price="10",
        principal="50",
        timestamp=TS,
        origin="import",
    )
    survivors = [StatementPosition(quantity=D("5"), ticker="KEEP")]
    proposals = run_checkpoint(account=accounts["holdings"], positions=survivors, as_of=AS_OF)
    assert [p.action_kind for p in proposals] == ["unexplained"]
    with pytest.raises(ValueError, match="no template mapping"):
        approve_proposal(proposals[0])
    reject_proposal(proposals[0], note="broker glitch; investigated")
    proposals[0].refresh_from_db()
    assert proposals[0].resolution == "rejected"
    # Resolved fingerprints stay resolved on re-run.
    assert run_checkpoint(account=accounts["holdings"], positions=survivors, as_of=AS_OF) == []


def test_empty_parse_against_nonempty_ledger_is_silenced(accounts, usd, held_oldco):
    """No holdings table parsed + ledger holds positions: not an
    assertable checkpoint (parser gap or format variant)."""
    assert run_checkpoint(account=accounts["holdings"], positions=[], as_of=AS_OF) == []


def test_settle_date_straddle_does_not_false_positive(accounts, usd):
    """A buy TRADED in-month but settling next month counts at the
    checkpoint (trade-date basis)."""
    inst = make_equity("STRAD", usd)
    eq.buy_shares(
        accounts=accounts,
        instrument=inst,
        quantity=10,
        price="5",
        principal="50",
        timestamp=datetime.datetime(2026, 2, 2, 20, 0, tzinfo=datetime.UTC),  # settle
        trade_timestamp=datetime.datetime(2026, 1, 30, 20, 0, tzinfo=datetime.UTC),
        origin="import",
    )
    assert (
        run_checkpoint(
            account=accounts["holdings"],
            positions=[StatementPosition(quantity=D("10"), ticker="STRAD")],
            as_of=AS_OF,
        )
        == []
    )
