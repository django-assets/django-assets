"""ADR-0029 review-queue resolution actions.

One best (rank 1, unresolved) proposal at a time; Reject advances;
exhaustion falls back to Materialize-new; SPLIT is destructive and
demands type-to-confirm. Proposals are re-validated on view and on
confirm — no time expiry, validity is checked when it matters.
"""

import uuid

from django.db import models
from django.db import transaction as db_transaction
from django.utils import timezone

from django_assets.brokerage.dedup import _criteria_for
from django_assets.brokerage.matching import find_asset_leg
from django_assets.brokerage.models import ImportLine, ImportLineProposal
from django_assets.brokerage.reconciliation import match_line
from django_assets.core.models import Transaction


def _resolve(proposal: ImportLineProposal, resolution: str) -> None:
    proposal.resolution = resolution
    proposal.resolved_at = timezone.now()
    proposal.save(update_fields=["resolution", "resolved_at"])


def _is_stale(proposal: ImportLineProposal) -> bool:
    """Candidate deleted (FK cascade handles that), edited, or its asset
    leg re-reconciled since scoring."""
    criteria, _schema = _criteria_for(proposal.line)
    if criteria is None:
        return True
    return find_asset_leg(proposal.candidate_transaction, criteria, proposal.line) is None


def current_proposal(line: ImportLine) -> ImportLineProposal | None:
    """The single best unresolved proposal, re-validated on view.
    Compound proposals outrank 1:1s at equal rank (their sum-match is
    the orchestrator's strongest signal)."""
    # Compound members carry a non-null group; NULLS LAST puts them first
    # on descending order in PG, so a rank-1 compound wins over a rank-1
    # single — the sum-match is the orchestrator's strongest signal.
    queue = line.proposals.filter(resolution="").order_by(
        "rank", models.F("proposal_group").desc(nulls_last=True)
    )
    for proposal in queue:
        if _is_stale(proposal):
            _resolve(proposal, "superseded")
            continue
        return proposal
    return None


def _confirm_pair(line: ImportLine, candidate: Transaction) -> None:
    criteria, _schema = _criteria_for(line)
    leg = find_asset_leg(candidate, criteria, line) if criteria else None
    if leg is None:
        raise ValueError("candidate is no longer valid for this line (stale)")
    match_line(line, [leg])


def confirm_proposal(proposal: ImportLineProposal) -> None:
    """Confirm 1:1 or COMBINE (all group members atomically)."""
    if _is_stale(proposal):
        _resolve(proposal, "superseded")
        raise ValueError("proposal went stale; the surface should advance")
    with db_transaction.atomic():
        if proposal.proposal_group:
            members = list(
                ImportLineProposal.objects.filter(proposal_group=proposal.proposal_group)
            )
            # COMBINE: ONE candidate leg reconciles ALL grouped lines, so
            # resolve it once — after the first add it would fail the
            # "not already matched" hard filter.
            first = members[0]
            criteria, _schema = _criteria_for(first.line)
            leg = (
                find_asset_leg(first.candidate_transaction, criteria, first.line)
                if criteria
                else None
            )
            if leg is None:
                raise ValueError("candidate is no longer valid (stale)")
            for member in members:
                match_line(member.line, [leg])
                _resolve(member, "confirmed")
            lines = [member.line_id for member in members]
        else:
            _confirm_pair(proposal.line, proposal.candidate_transaction)
            _resolve(proposal, "confirmed")
            lines = [proposal.line_id]
        ImportLineProposal.objects.filter(line_id__in=lines, resolution="").update(
            resolution="superseded", resolved_at=timezone.now()
        )


def confirm_split(proposal_group: uuid.UUID, *, confirmation: str) -> None:
    """Destructive SPLIT (ADR-0029 replace semantic): deletes the user's
    manual Transaction — notes and metadata included — then materializes
    each grouped line. The caller must render the full manual and pass
    confirmation='replace' (type-to-confirm; one click is not enough)."""
    if confirmation != "replace":
        raise ValueError(
            'confirm_split is destructive: pass confirmation="replace" '
            "after showing the user the full manual Transaction"
        )
    members = list(ImportLineProposal.objects.filter(proposal_group=proposal_group, resolution=""))
    if not members:
        raise ValueError("no unresolved proposals in this group (stale?)")
    manual = members[0].candidate_transaction
    with db_transaction.atomic():
        # Supersede other proposals referencing the manual BEFORE deleting
        # (the FK cascade removes rows pointing at it).
        ImportLineProposal.objects.filter(candidate_transaction=manual).exclude(
            pk__in=[member.pk for member in members]
        ).update(resolution="superseded", resolved_at=timezone.now())
        lines = [member.line for member in members]
        for line in lines:
            line.metadata["split"] = {
                "group": str(proposal_group),
                "replaced_manual_description": manual.description,
            }
        manual.delete()  # cascades the group's proposal rows too
        for line in lines:
            _materialize(line)


def reject_proposal(proposal: ImportLineProposal) -> ImportLineProposal | None:
    """Reject-advance: mark rejected, return the next best unresolved
    proposal, or None when exhausted (caller falls back to
    materialize_new). Compound rejection advances members independently."""
    with db_transaction.atomic():
        if proposal.proposal_group:
            ImportLineProposal.objects.filter(
                proposal_group=proposal.proposal_group, resolution=""
            ).update(resolution="rejected", resolved_at=timezone.now())
        else:
            _resolve(proposal, "rejected")
    return current_proposal(proposal.line)


def materialize_new(line: ImportLine) -> list[Transaction]:
    """Fast path: none of the candidates is right — this is a new event."""
    line.proposals.filter(resolution="").update(resolution="rejected", resolved_at=timezone.now())
    return _materialize(line)


def override_match(line: ImportLine, transaction: Transaction) -> None:
    """User picks ANY transaction (cross-account/day/batch). Same effect
    as Confirm 1:1 plus a synthetic audit proposal row."""
    from django_assets.brokerage.accounts import account_allows_reconciliation

    legs = [
        leg
        for leg in transaction.legs.select_related("account")
        if account_allows_reconciliation(leg.account)
    ]
    if not legs:
        raise ValueError("override target has no reconcilable legs")
    with db_transaction.atomic():
        match_line(line, legs)
        line.proposals.filter(resolution="").update(
            resolution="superseded", resolved_at=timezone.now()
        )
        ImportLineProposal.objects.update_or_create(
            line=line,
            candidate_transaction=transaction,
            defaults={
                "score_total": 0.0,
                "score_breakdown": {"user_override": True},
                "rank": 1,
                "resolution": "confirmed",
                "resolved_at": timezone.now(),
            },
        )


def _materialize(line: ImportLine) -> list[Transaction]:
    from django_assets.brokerage.imports import materialize_line_now

    return materialize_line_now(line)
