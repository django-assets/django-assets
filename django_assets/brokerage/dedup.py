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
    from django_assets.brokerage.models import ImportLine


def propose_candidates(line: "ImportLine") -> list[object]:
    """ADR-0029 §8.1 hook: candidate duplicate transactions for a line.

    Milestone B7 delivers the hybrid-scoring implementation and the
    ImportLineProposal review flow; until then nothing is proposed and
    every matchable line materializes directly.
    """
    return []


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
