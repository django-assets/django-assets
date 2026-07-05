"""B7: compound COMBINE/SPLIT detection (ADR-0029 cardinalities)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportLineProposal
from django_assets.brokerage.review import confirm_proposal, confirm_split, current_proposal
from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Transaction

pytestmark = pytest.mark.ledger

D = Decimal

SELL_DATE = datetime.datetime(2026, 3, 10, 20, 0, tzinfo=datetime.UTC)


def two_fill_csv(q1, a1, q2, a2):
    """Two same-day partial fills of one sell order."""
    header = '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'
    return "\n".join(
        [
            header,
            f'"03/10/2026","Sell","AAPL","APPLE INC","{q1}","200.00","0.00","{a1}"',
            f'"03/10/2026","Sell","AAPL","APPLE INC","{q2}","200.00","0.00","{a2}"',
            "",
        ]
    )


@pytest.fixture
def manual_sale(accounts, usd, aapl):
    """User entered 'sold 1000 AAPL @ $200' as one trade."""
    with TransactionBuilder(
        account=accounts["cash"], timestamp=SELL_DATE, description="sold 1000 AAPL"
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="200000.00")
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount="-1000")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-200000.00")
        b.add_leg(account=accounts["market"], instrument=aapl, amount="1000")
    return b.transaction


def test_combine_partial_fills(batch, accounts, usd, aapl, manual_sale):
    """600 + 400 fills against the single manual 1000-share sale."""
    process_batch(batch, two_fill_csv(600, "120000.00", 400, "80000.00"))
    compound = ImportLineProposal.objects.filter(proposal_group__isnull=False)
    assert compound.count() == 2  # one member per line
    assert {p.compound_kind for p in compound} == {"combine"}
    groups = {p.proposal_group for p in compound}
    assert len(groups) == 1
    # Neither line materialized while awaiting review.
    assert Transaction.objects.filter(origin="import").count() == 0

    confirm_proposal(compound.first())
    for proposal in ImportLineProposal.objects.filter(proposal_group__in=groups):
        assert proposal.resolution == "confirmed"
    for line in batch.lines.all():
        matched = {leg.pk for leg in line.matched_legs.all()}
        assert manual_sale.legs.get(account=accounts["cash"]).pk in matched
    assert Transaction.objects.filter(origin="import").count() == 0  # no dup


def test_compound_cap_at_four(batch, accounts, usd, aapl, manual_sale):
    """5+ same-day fills are not auto-grouped (combinatorial bound)."""
    header = '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'
    rows = [
        '"03/10/2026","Sell","AAPL","APPLE INC","200","200.00","0.00","40000.00"' for _ in range(5)
    ]
    process_batch(batch, "\n".join([header, *rows, ""]))
    assert not ImportLineProposal.objects.filter(proposal_group__isnull=False).exists()


def test_split_destructive_confirm(batch, accounts, usd, aapl):
    """One manual dividend vs two broker rows; SPLIT deletes the manual
    (type-to-confirm) and materializes the components."""
    with TransactionBuilder(
        account=accounts["cash"],
        timestamp=SELL_DATE,
        description="quarterly dividend, hand-entered",
        metadata={"note": "user's precious note"},
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="200000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-200000.00")
    manual = b.transaction

    process_batch(batch, two_fill_csv(600, "120000.00", 400, "80000.00"))
    group = ImportLineProposal.objects.filter(proposal_group__isnull=False).first()
    assert group is not None

    with pytest.raises(ValueError, match="replace"):
        confirm_split(group.proposal_group, confirmation="yes")  # wrong word

    confirm_split(group.proposal_group, confirmation="replace")
    assert not Transaction.objects.filter(pk=manual.pk).exists()  # deleted
    assert Transaction.objects.filter(origin="import").count() == 2
    for line in batch.lines.all():
        assert line.matched_legs.count() > 0


def test_compound_alternatives_kept(batch, accounts, usd, aapl, manual_sale):
    """1:1 proposals against OTHER candidates stay stored alongside the
    compound (the same-candidate 1:1 is absorbed into the compound row —
    unique (line, candidate)); the surface shows the compound first."""
    with TransactionBuilder(
        account=accounts["cash"],
        timestamp=SELL_DATE + datetime.timedelta(days=1),
        description="a different plausible sale",
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="119000.00")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-119000.00")
    process_batch(batch, two_fill_csv(600, "120000.00", 400, "80000.00"))
    line = batch.lines.get(line_number=1)
    best = current_proposal(line)
    assert best.proposal_group is not None  # compound outranks the 1:1s
    singles = ImportLineProposal.objects.filter(line=line, proposal_group__isnull=True)
    assert singles.exists()  # the other candidate's 1:1 kept as alternative
