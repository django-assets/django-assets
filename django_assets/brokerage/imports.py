"""The import orchestrator (brokerage spec §5.3, ADR-0027) and the
batch-aware bulk wrapper (§5.4).

process_batch persists raw evidence FIRST (crash-safe re-processing),
then materializes matchable lines and self-reconciles eligible legs
into matched_legs (ADR-0024 Path 1). Schemas stay dumb about ledger
state; eligibility is account_allows_reconciliation (D-10).
"""

from typing import Any

from django.db import transaction as db_transaction

from django_assets.brokerage.accounts import account_allows_reconciliation
from django_assets.brokerage.dedup import detect_compounds, propose_candidates
from django_assets.brokerage.models import ImportBatch, ImportLine, TransactionImport
from django_assets.core.builder import BulkImportResult, TransactionBuilder
from django_assets.core.models import Transaction


def process_batch(batch: ImportBatch, source: Any) -> ImportBatch:
    schema = batch.get_schema()

    # 1. Raw evidence lands before any materialization (invariant 5).
    if not batch.lines.exists():
        ImportLine.objects.bulk_create(schema.parse_batch(batch, source))

    # 2. Propose (ADR-0029): per-line 1:1 candidates, then the batch-level
    #    same-day compound pass. Lines with proposals wait for review.
    pending: set[int] = set()
    for line in batch.lines.order_by("line_number"):
        if not line.is_matchable or line.metadata.get("materialized"):
            continue
        if propose_candidates(line):
            pending.add(line.pk)
    detect_compounds(batch)
    pending.update(batch.lines.filter(proposals__resolution="").values_list("pk", flat=True))

    # 3. Materialize matchable, unprocessed lines without proposals.
    created: list[Transaction] = []
    for line in batch.lines.order_by("line_number"):
        if not line.is_matchable or line.metadata.get("materialized"):
            continue
        if line.pk in pending:
            continue  # awaiting review
        with db_transaction.atomic():
            transactions = schema.materialize_line(line)
            Transaction.objects.filter(pk__in=[tx.pk for tx in transactions]).exclude(
                origin="import"
            ).update(origin="import")
            eligible = [
                leg
                for tx in transactions
                for leg in tx.legs.select_related("account")
                if account_allows_reconciliation(leg.account)
            ]
            line.matched_legs.add(*eligible)
            line.metadata["materialized"] = [tx.pk for tx in transactions]
            line.save(update_fields=["metadata"])
        created.extend(transactions)

    # 4. End-of-batch bookkeeping + the ADR-0029 invariant.
    batch.transaction_count = len(created)
    batch.save(update_fields=["transaction_count"])
    _assert_no_unmatched_eligible_legs(batch, created)
    return batch


def materialize_line_now(line: ImportLine) -> list[Transaction]:
    """Materialize one line (shared by the orchestrator and the review
    actions): templates run, origin becomes import, eligible legs
    self-reconcile, and the line records its transactions."""
    schema = line.batch.get_schema()
    with db_transaction.atomic():
        transactions: list[Transaction] = schema.materialize_line(line)
        Transaction.objects.filter(pk__in=[tx.pk for tx in transactions]).exclude(
            origin="import"
        ).update(origin="import")
        eligible = [
            leg
            for tx in transactions
            for leg in tx.legs.select_related("account")
            if account_allows_reconciliation(leg.account)
        ]
        line.matched_legs.add(*eligible)
        line.metadata["materialized"] = [tx.pk for tx in transactions]
        line.save(update_fields=["metadata"])
    return transactions


def _assert_no_unmatched_eligible_legs(batch: ImportBatch, transactions: list[Transaction]) -> None:
    matched_ids = set(ImportLine.objects.filter(batch=batch).values_list("matched_legs", flat=True))
    for tx in transactions:
        for leg in tx.legs.select_related("account"):
            if account_allows_reconciliation(leg.account) and leg.pk not in matched_ids:
                raise RuntimeError(
                    f"import invariant violated: eligible leg {leg.pk} of "
                    f"transaction {tx.pk} is outside matched_legs"
                )


def import_transactions(
    rows: list[dict[str, Any]],
    *,
    batch: ImportBatch,
    batch_size: int = 1000,
    on_error: str = "raise",
) -> BulkImportResult:
    """Batch-aware wrapper over TransactionBuilder.bulk_import (ADR-0019):
    every created Transaction gets a TransactionImport linking it to
    `batch`; rows may carry `_import_external_id` / `_import_source_data`.
    """
    result = TransactionBuilder.bulk_import(
        rows,
        batch_size=batch_size,
        on_error=on_error,  # type: ignore[arg-type]
    )
    imported_rows = _surviving_rows(rows, result)
    links = [
        TransactionImport(
            transaction=tx,
            batch=batch,
            external_id=row.get("_import_external_id", ""),
            source_data=row.get("_import_source_data", {}),
        )
        for tx, row in zip(result.transactions, imported_rows, strict=True)
    ]
    TransactionImport.objects.bulk_create(links)
    batch.transaction_count = batch.transaction_count + result.inserted
    batch.save(update_fields=["transaction_count"])
    return result


def _surviving_rows(rows: list[dict[str, Any]], result: BulkImportResult) -> list[dict[str, Any]]:
    failed = {error.index for error in result.errors}
    return [row for index, row in enumerate(rows) if index not in failed]
