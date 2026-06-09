# ADR-0025: Broker download lines — storage and matching workflow

## Status

Accepted — 2026-06-07

Constraints settled going into this:

1. **Placement** (per ADR-0024): all reconciliation models live in `django_assets.brokerage`. Approaches that put any reconciliation model in core are off the table.
2. **No user-attestation path** (per ADR-0024 as simplified): every `ImportLine` corresponds to a real broker import (CSV, OFX, QFX, automatic transaction download, etc.). Manually-entered transactions without a corresponding broker import remain unreconciled — that's fine, they're still valid Transactions. This rules out Approach 3 (separate `UserAttestation` model) entirely.
3. **FK location → M2M** (per ADR-0026): `ImportLine.matched_legs = ManyToManyField(TransactionLeg)` with an auto-generated join table. No through model.
4. **Reconciliation scope** (per ADR-0024): only legs whose accounts are broker-reported asset accounts (brokerage cash, brokerage holdings) are reconciled. A single CSV row often contains commission and fee data, but the corresponding commission/fee legs in the resulting Transaction are NOT reconciled — they remain editable.

## Context

When the user downloads a broker statement (CSV, OFX, QFX, JSON, etc.), the file contains many rows. Some rows map cleanly to single ledger Transactions; others don't (multi-leg statement lines, summary rows, balance-snapshot rows). Some rows represent activity the user has already entered manually; others represent activity the user didn't yet know about.

The ledger-creation half of this story is covered by ADR-0019: `bulk_import` in core, `ImportBatch` + `TransactionImport` + dedup helpers in the brokerage sub-package. That covers "how do I turn a CSV into Transactions in the ledger."

What ADR-0019 does **not** cover is the **per-row storage and lifecycle** of the broker download data itself. Specifically:

1. **Keep raw rows as evidence.** When a leg is reconciled (per ADR-0024), the user wants to be able to look back at the exact row in the broker download that confirmed it. That row needs to live somewhere, not just be ephemerally parsed during the import.

2. **Track matched vs. unmatched state.** A broker download may contain rows that don't match anything in the ledger yet (the user hasn't entered the corresponding manual transaction yet, or the importer couldn't find a match). The user wants to see a queue of "unmatched broker lines awaiting reconciliation" to work through.

3. **Bidirectional reconciliation state.** Per ADR-0024, a `TransactionLeg` has `reconciled_by` pointing at a `ReconciliationSource`. For broker downloads, the matched-versus-unmatched state of each individual line is the inverse view: instead of asking "is this leg reconciled?", you ask "is this download line matched?" Both views should stay consistent.

4. **Unflip → unmatched.** When a user unflips a reconciled leg (per ADR-0024), the line that previously reconciled it returns to the unmatched pool, available for re-matching against a different leg or remaining unmatched until the user fixes the underlying mistake.

5. **Late-arriving manual entries.** The user might enter a transaction manually before the broker download arrives. When the download lands, the importer (or a manual-match UI) should be able to match the existing manual entry's leg to a newly-arrived line, instead of creating a duplicate Transaction.

This ADR is about how to model the per-row data and the matching workflow.

## Decision

Adopt **Approach 1**: a single `ImportLine` model in `django_assets.brokerage` that holds both the raw row and the reconciliation linkage, with an auto-generated M2M to `core.TransactionLeg` (per ADR-0026):

```python
class ImportLine(models.Model):
    batch = models.ForeignKey(ImportBatch, related_name="lines", on_delete=models.CASCADE)
    line_number = models.PositiveIntegerField()
    raw_data = models.JSONField()
    kind = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_legs = models.ManyToManyField(
        "django_assets.TransactionLeg",
        related_name="reconciliation_lines",
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "line_number"],
                name="uniq_importline_batch_linenumber",
            ),
        ]
```

The remaining sub-questions resolve as follows.

### Raw row storage: verbatim JSON only

`raw_data` holds the row exactly as the broker provided it. No pre-parsed denormalized columns. The raw row is the canonical evidence, and anything the importer extracted (instrument, date, amount, direction) lives on the resulting `Transaction` / `TransactionLeg` — which the M2M reaches whenever the line is matched. Unmatched-line queries that need to filter by instrument or date use JSONB path lookups against `raw_data`. If that becomes a performance issue, denorm columns can be added later via migration.

### Informational rows: stored, not filtered

Balance snapshots, year-to-date summaries, dividend-rate-only lines, and other non-transactional rows all become `ImportLine` records with distinct `kind` values (e.g., `balance_snapshot`, `ytd_summary`, `dividend_rate`). Matchable kinds use the `broker_` prefix (`broker_csv`, `broker_qfx`, `broker_ofx`, `broker_auto`, …); informational kinds use their own prefixes. The unmatched-queue query filters by matchable kinds:

```python
ImportLine.objects.filter(
    kind__startswith="broker_",
    matched_legs__isnull=True,
)
```

Storing informational rows preserves complete evidence and enables future balance-reconciliation work (comparing the broker's stated end-of-day balance to the ledger's computed holdings).

### Unmatched-lines UX: admin + DRF in brokerage

`django_assets.brokerage` ships:

- An admin changelist with matched/unmatched filtering and a manual-match action that adds one or more `TransactionLeg`s to `ImportLine.matched_legs`.
- DRF endpoints for listing unmatched lines, retrieving line detail, and posting matches between a line and one or more legs.

Host-app screens (a dedicated "reconcile" page, for example) are out of scope. The host wraps the DRF endpoints however it likes. This mirrors ADR-0017's split between package-shipped admin/DRF surfaces and host-built UI.

## Sub-questions

1. **What's the unit of storage?** One record per row in the broker file? One record per leg of the resulting Transaction? Both?
2. **Where do the records live?** In the brokerage sub-package? In core as a generic "import line" primitive? In a host-app model?
3. **What's the relationship to `ReconciliationSource` (ADR-0024)?** Is `ReconciliationSource` derived from a download line? Is the download line itself the `ReconciliationSource`? Or are they distinct related models?
4. **How is matched/unmatched state queried?** A boolean field on the line? Implied by a non-null FK to a `TransactionLeg` (or `ReconciliationSource`)? A separate queryset method?
5. **What happens to unmatched lines over time?** Do they persist indefinitely, or are they archived after some period? Do they show up in a perpetual "to-reconcile" list?
6. **What about lines that aren't expected to match anything?** Some statement rows are informational only (running balance, dividend rate, year-to-date summary). Are they stored as `ImportLine` records too, or filtered out at import time?
7. **Multi-leg matches.** A single broker line can correspond to multiple ledger legs (an internal transfer between two of the user's accounts; an option exercise that affects shares, cash, and option contract balances). Is the line-to-leg relationship one-to-many?
8. **How does the user re-match an unmatched line?** Admin action? Brokerage helper API? Host-app UI? All three?

## Alternatives

### Approach 1: Single `ImportLine` model (leading proposal)

`django_assets.brokerage` ships one `ImportLine` model that serves as both the raw-row record AND the reconciliation linkage:

```python
class ImportLine(models.Model):
    batch = models.ForeignKey(ImportBatch, related_name="lines", on_delete=models.CASCADE)
    line_number = models.PositiveIntegerField()
    raw_data = models.JSONField()
    kind = models.CharField(max_length=40)           # "broker_csv", "broker_qfx", "broker_ofx", "broker_auto", "balance_snapshot", ...
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_legs = models.ManyToManyField(
        "django_assets.TransactionLeg",
        related_name="reconciliation_lines",
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
```

- Matched: `matched_legs.exists()`. The matched legs' accounts are always broker-reported asset accounts (cash or holdings) — non-asset legs (commission, fee, counterparty) are never in the M2M, even when they were derived from the same CSV row.
- Unmatched: `not matched_legs.exists()`.
- Multi-asset CSV rows (e.g., AAPL buy: cash debit + holdings credit): both asset legs are added to `matched_legs` of the same `ImportLine` (per ADR-0026).
- All `ImportLine` rows have a real `batch` and `line_number` — no nullable variants. The user-attestation path was eliminated by ADR-0024.

**Pros**: one model, one concept; no nullable fields; binary reconciled state via the M2M; consistent with all the other ADR decisions.
**Cons**: model carries both "raw evidence from a file" and "reconciliation linkage" semantics in one place. Functionally fine, just a slightly multi-purpose model.

### Approach 2: Two models — `ImportLine` (raw rows) + separate `Reconciliation` linkage model

~~Originally proposed two models with the FK to `TransactionLeg` on a separate `Reconciliation` linkage row.~~ Ruled out by ADR-0026 (M2M-on-`ImportLine` accepted; no separate linkage model).

### Approach 3: `ImportLine` + separate `UserAttestation` model

~~Originally proposed to handle the user-attestation case separately from broker downloads.~~ Ruled out by ADR-0024 (user-attestation path eliminated; all reconciliation requires a real broker import).

### Approach 4: Store raw rows in `ImportBatch.metadata` as JSON; no per-row records (rejected)

Don't model per-row state at all. The `ImportBatch.metadata` JSONField stores the parsed rows; reconciliation goes through a much smaller `Reconciliation` model that just links a leg to a (batch_id, line_number) pair via JSON references.

**Pros**: minimal schema.
**Cons**: matched/unmatched queries become JSONB lookups; no clean way to add per-line data over time; admin views are awkward. Incompatible with ADR-0026's M2M decision — there's no per-row Python object for the M2M to attach to.

## Consequences

**Easier:**

- Single model for all broker-derived reconciliation. One queryset, one admin, one signal-handler target.
- "Is this leg reconciled?" is `leg.reconciliation_lines.exists()` — clean in admin, DRF, and signal handlers.
- Per-row evidence preserved verbatim. Reconciled leg → original broker-statement row is always one M2M traversal away.
- The unmatched-lines queue is `ImportLine.objects.filter(kind__startswith="broker_", matched_legs__isnull=True)` — informational rows (non-`broker_` kinds) are excluded by construction.
- Re-matching workflows operate on a stable, queryable population of rows. Unflipping a leg just calls `line.matched_legs.remove(leg)`; the line returns to the unmatched queue automatically.
- Multi-asset-leg CSV rows (e.g., a buy that affects both cash and holdings) are first-class via the M2M (per ADR-0026) — no row duplication, no nullable join fields.
- Per ADR-0024, all of this lives in brokerage — core stays untouched.
- The "only asset-account legs are reconciled" rule is naturally expressed: the importer only adds asset-side legs to `matched_legs`; commission and fee legs from the same CSV row are never in the M2M and remain editable.
- Informational rows (balance snapshots, YTD summaries) are first-class records, opening the door to balance-reconciliation features later without a schema migration.

**Harder:**

- Unmatched-line queries that need to filter by instrument, date, or amount must use JSONB path lookups against `raw_data` (no denormalized columns by choice). If unmatched-queue performance becomes a real problem, denorm columns can be added later via migration. We accept the trade-off because the canonical place to query parsed data is the resulting `Transaction` / `TransactionLeg`, which the M2M reaches for matched lines.
- Signal handlers needed to enforce the reconciliation lock on `TransactionLeg` (already specified by ADR-0024; this ADR just shapes the model the signals query).
- Kind taxonomy needs to be documented and disciplined: matchable kinds use the `broker_` prefix; informational kinds use their own prefixes. Drift here breaks the unmatched-queue filter.
- Storage cost for the raw row data (JSON); not significant for retail-scale data, may matter for institutional volumes.

## Related

- ADR-0019 (Bulk import primitives; import management) — establishes `ImportBatch` and `TransactionImport`. This ADR adds per-row granularity below the Transaction level.
- ADR-0024 (Reconciliation scope) — establishes that the reconciliation system lives in brokerage and that the FK points from brokerage to core's `TransactionLeg`. This ADR settles the precise model shape of that linkage.
- ADR-0020 (Core ships only numeric integrity) — informs the choice of where reconciliation models live (brokerage, not core).
