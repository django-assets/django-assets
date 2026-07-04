"""Host-driven pre-flight dedup helpers (brokerage spec §5.4, ADR-0019)
and the orchestrator's proposal hook (ADR-0029).

Policy is per-import, never per-account: the package never auto-applies
a strategy; the host's import code calls the helper that fits its data.
"""

import datetime
from typing import TYPE_CHECKING

from django.db import transaction as db_transaction

from django_assets.brokerage.models import ImportBatch, TransactionImport
from django_assets.core.models import Account, Transaction

if TYPE_CHECKING:
    from django_assets.brokerage.matching import MatchCriteria
    from django_assets.brokerage.models import ImportLine, ImportLineProposal
    from django_assets.brokerage.schemas import ImportSchema


def _criteria_for(
    line: "ImportLine",
) -> "tuple[MatchCriteria | None, ImportSchema]":
    """MatchCriteria for a matchable line, or None when the schema is
    pure-informational (match_criteria unimplemented)."""
    schema = line.batch.get_schema()
    try:
        return schema.match_criteria(line), schema
    except NotImplementedError:
        return None, schema


def propose_candidates(line: "ImportLine") -> list["ImportLineProposal"]:
    """ADR-0029: store ranked 1:1 proposals for a line (hard filters,
    soft scoring, threshold discard, max_proposals cap). Returns the
    stored unresolved proposals; empty means materialize directly."""
    from django_assets.brokerage.matching import hard_filter_candidates, score_candidate
    from django_assets.brokerage.models import ImportLineProposal

    criteria, schema = _criteria_for(line)
    if criteria is None:
        return []

    scored = []
    for candidate, leg in hard_filter_candidates(line, criteria, schema):
        score = score_candidate(criteria, candidate, leg, schema)
        if score.total <= schema.max_score_to_propose:
            scored.append((score, candidate))
    scored.sort(key=lambda pair: pair[0].total)

    proposals = []
    for rank, (score, candidate) in enumerate(scored[: schema.max_proposals], start=1):
        proposal, _ = ImportLineProposal.objects.get_or_create(
            line=line,
            candidate_transaction=candidate,
            defaults={
                "score_total": score.total,
                "score_breakdown": score.breakdown,
                "rank": rank,
            },
        )
        proposals.append(proposal)
    return proposals


def detect_compounds(batch: ImportBatch) -> None:
    """Same-day-in-batch COMBINE/SPLIT detection (ADR-0029): group
    unmatched broker lines by (trade_date, instrument, account, sign);
    when a group of 2–4 lines sums to a candidate manual's amount, emit
    one compound proposal group (members share a UUID). The 1:1
    proposals stay stored as alternatives."""
    import uuid
    from collections import defaultdict

    from django_assets.brokerage.matching import hard_filter_candidates
    from django_assets.brokerage.models import ImportLineProposal

    groups: dict[tuple[object, ...], list[tuple]] = defaultdict(list)  # type: ignore[type-arg]
    for line in batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True):
        if line.metadata.get("materialized"):
            continue
        criteria, schema = _criteria_for(line)
        if criteria is None:
            continue
        key = (
            criteria.date,
            criteria.instrument.pk,
            batch.account_id,
            criteria.amount >= 0,
        )
        groups[key].append((line, criteria, schema))

    for members in groups.values():
        if not 2 <= len(members) <= 4:
            continue  # the auto-grouping cap bounds the search space
        total = sum(criteria.amount for _, criteria, _ in members)
        first_line, first_criteria, schema = members[0]
        summed = type(first_criteria)(
            date=first_criteria.date,
            instrument=first_criteria.instrument,
            amount=total,
            compound_hint=first_criteria.compound_hint,
        )
        for candidate, leg in hard_filter_candidates(first_line, summed, schema):
            if leg.amount != total:
                continue  # exact sum-match only
            group_id = uuid.uuid4()
            for line, _criteria, _schema in members:
                ImportLineProposal.objects.update_or_create(
                    line=line,
                    candidate_transaction=candidate,
                    defaults={
                        "score_total": 0.0,
                        "score_breakdown": {"compound_sum_match": 0.0},
                        "rank": 1,
                        "proposal_group": group_id,
                        "compound_kind": first_criteria.compound_hint,
                    },
                )
            break  # one compound proposal per group


def is_period_imported(
    account: Account,
    broker: str,
    document_kind: str,
    period_start: datetime.date,
    period_end: datetime.date,
) -> bool:
    """True when an existing batch OVERLAPS the period. Keyed on
    (account, broker, document_kind); format/version deliberately
    excluded — a re-download in a new format is still the same period."""
    return ImportBatch.objects.filter(
        account=account,
        schema_broker=broker,
        schema_document_kind=document_kind,
        period_start__lte=period_end,
        period_end__gte=period_start,
    ).exists()


def get_imported_periods(
    account: Account, broker: str, document_kind: str
) -> list[tuple[datetime.date, datetime.date]]:
    periods = ImportBatch.objects.filter(
        account=account,
        schema_broker=broker,
        schema_document_kind=document_kind,
        period_start__isnull=False,
        period_end__isnull=False,
    ).order_by("period_start")
    # The isnull filters guarantee non-null pairs; the stubs can't see that.
    return [
        (start, end)
        for start, end in periods.values_list("period_start", "period_end")
        if start is not None and end is not None
    ]


def delete_import_batch(batch: ImportBatch) -> dict[str, int]:
    """Period replacement: remove the batch AND the Transactions it
    created (whole-transaction deletes keep the trigger satisfied).
    Lines, TransactionImports, and proposals cascade with the batch."""
    with db_transaction.atomic():
        for line in batch.lines.all():
            line.matched_legs.clear()  # unflip: the lock guards deletion
        tx_ids = list(
            TransactionImport.objects.filter(batch=batch).values_list("transaction_id", flat=True)
        )
        materialized = [
            pk for line in batch.lines.all() for pk in line.metadata.get("materialized", [])
        ]
        _, per_model = Transaction.objects.filter(pk__in=tx_ids + materialized).delete()
        batch.delete()
    return {"transactions": per_model.get("django_assets.Transaction", 0)}


def find_by_external_ids(
    account: Account, broker: str, document_kind: str, external_ids: list[str]
) -> set[str]:
    """Stable-ID pre-flight: which of these ids are already imported?"""
    return set(
        TransactionImport.objects.filter(
            batch__account=account,
            batch__schema_broker=broker,
            batch__schema_document_kind=document_kind,
            external_id__in=external_ids,
        ).values_list("external_id", flat=True)
    )


def is_file_imported(account: Account, file_hash: str) -> bool:
    return ImportBatch.objects.filter(account=account, file_hash=file_hash).exists()
