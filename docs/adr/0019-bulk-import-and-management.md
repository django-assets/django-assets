# ADR-0019: Bulk import primitives in core; import management in brokerage

## Status

Accepted — 2026-06-03

## Context

Historical data imports — broker statements, migrations from other portfolio trackers, backtest scenario seeding — are a critical workflow for any ledger system. They share three concerns:

1. **Performance.** Naive insertion (one DB transaction per source row, one INSERT per leg, cachalot invalidation per write) is orders of magnitude slower than batched insertion using `bulk_create`, deferred trigger handling, and a single cachalot invalidation per batch.
2. **Correctness.** The deferred balance trigger from ADR-0004 enforces per-instrument zero-sum per Transaction. Batched imports must compose correctly with the trigger — multiple Transactions can be inserted in one DB transaction provided each individual Transaction's legs balance.
3. **Dedup.** Re-importing the same broker statement, or importing overlapping periods, must not silently duplicate transactions. Different source formats provide different signals for dedup (stable IDs, period boundaries, content hashes); the package must support the common patterns without prescribing a single strategy.

Two design decisions sit on top of these concerns:

**Which decisions belong in core, which in a sibling sub-package?** Per ADR-0011, core is the ledger primitive — integrity-only, policy-free. Import dedup is policy; it depends on what the source data provides and what the host wants. The efficient insertion mechanics, by contrast, are pure ledger correctness — they belong in core. The clean split is: core ships the efficient insertion primitive; brokerage ships the dedup machinery and per-import audit trail.

**How prescriptive should the dedup API be?** Earlier design proposed an `idempotency_key=` parameter on `bulk_import` that would do metadata-based dedup automatically. Design review identified that many real broker sources (Schwab CSV being the canonical example) expose no stable transaction ID at all, making this approach a poor default. The simpler model is: dedup happens before `bulk_import` is called, using whichever strategy fits the source. The package ships building blocks; the host composes the policy.

Four dedup patterns emerged as the realistic ones for retail and institutional source data:

- **Period-discipline** — imports are restricted to whole time periods (whole days, whole months, etc.). The host tracks what periods have been imported per `(account, schema_broker, schema_document_kind)`. Re-imports of a covered period are detected by date arithmetic alone. Works for any source that publishes whole-period statements, including those with no stable IDs.
- **Period-replacement** — when a known-bad period needs re-importing, the host deletes the range and re-imports. The deferred balance trigger correctly handles whole-transaction DELETE (per ADR-0006), so range deletion is safe.
- **Metadata-key idempotency** — for sources that publish stable IDs (Interactive Brokers Flex Query, modern broker APIs), filter source rows against existing TransactionImport rows by external_id before bulk_import.
- **File-level dedup** — hash the source file and refuse to re-import the same file. Useful as a coarse safety net regardless of which finer-grained pattern is used.

The package supports all four by providing models and helpers; the host picks per `(account, schema_broker, schema_document_kind)`.

## Decision

### Core (the `django_assets.core` sub-package) ships two primitives

```python
class TransactionBuilder:
    @classmethod
    def bulk_import(
        cls,
        rows: Iterable["TransactionDict"],
        *,
        batch_size: int = 1000,
        on_error: Literal["raise", "skip", "collect"] = "raise",
    ) -> "BulkImportResult":
        """Efficient batched insertion of pre-validated transaction dicts.

        Each row is a dict with keys: timestamp, trade_timestamp (optional),
        description (optional), metadata (optional), legs (list of leg dicts).

        Performance:
        - One DB transaction per batch_size rows.
        - bulk_create for both Transaction and TransactionLeg.
        - Single cachalot invalidation per batch.
        - Deferred balance trigger validates each Transaction's leg balance at COMMIT.

        Error handling:
        - "raise": stops on the first error; partial batch rollback.
        - "skip": logs and continues; failed rows in result.errors.
        - "collect": same as "skip" but the result.errors list is returned for inspection.

        Returns BulkImportResult dataclass:
            inserted: int
            failed: int
            errors: list[BulkImportError]
        """

    @classmethod
    def delete_range(
        cls,
        account: "Account",
        from_: datetime,
        to_: datetime,
        *,
        confirm: bool = False,
    ) -> int:
        """Delete Transactions for the account where timestamp is in [from_, to_).

        Safety brake: refuses unless confirm=True. Hosts must opt in explicitly
        to range deletion; UI code should require the user to type "delete" or
        similar before passing confirm=True.

        The deferred balance trigger fires on DELETE; whole-transaction deletion
        keeps per-instrument sums at zero (per ADR-0006), so the trigger passes.

        Returns the count of deleted Transactions.
        """
```

Core does NOT ship:

- ImportBatch model
- TransactionImport model
- Any dedup logic
- Any source-format awareness
- Any file-hash tracking

These are policy concerns and live in brokerage.

### Brokerage (the `django_assets.brokerage` sub-package) ships the management layer

```python
class ImportBatch(models.Model):
    """One import operation. Groups transactions imported together."""
    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(
        "django_assets.Account", on_delete=models.CASCADE, related_name="import_batches",
    )
    # Schema-key tuple per ADR-0027 (registered ImportSchema).
    # Together these resolve which Python class parsed this batch.
    schema_broker = models.CharField(max_length=40, db_index=True)        # "schwab", "fidelity", "ib", ...
    schema_document_kind = models.CharField(max_length=40, db_index=True) # "trades", "dividends", "balances", ...
    schema_format_kind = models.CharField(max_length=20)                  # "csv", "qfx", "ofx", "flex", "json"
    schema_version = models.CharField(max_length=20)                      # broker-defined version tag, e.g. "2026.01"

    period_start = models.DateTimeField(null=True, blank=True, db_index=True)
    period_end = models.DateTimeField(null=True, blank=True, db_index=True)

    file_name = models.CharField(max_length=255, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)

    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )

    transaction_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)


class TransactionImport(models.Model):
    """Per-transaction import provenance. Optional one-to-one with Transaction."""
    transaction = models.OneToOneField(
        "django_assets.Transaction",
        on_delete=models.CASCADE, related_name="import_meta",
    )
    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="transaction_imports",
    )
    external_id = models.CharField(max_length=200, blank=True, db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    source_data = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "external_id"],
                condition=~models.Q(external_id=""),
                name="uniq_batch_external_id",
            ),
        ]
        indexes = [
            models.Index(fields=["external_id"]),
            models.Index(fields=["content_hash"]),
        ]
```

### Brokerage ships dedup helpers

```python
# Period-discipline pattern
def is_period_imported(
    account, schema_broker, schema_document_kind, period_start, period_end,
) -> bool:
    """True if an ImportBatch fully covers [period_start, period_end]
    for (account, schema_broker, schema_document_kind). Format and version
    are intentionally NOT part of the dedup scope: a Schwab trades CSV v1
    and a Schwab trades CSV v2 cover the same trade-period concept."""

def get_imported_periods(
    account, schema_broker=None, schema_document_kind=None,
) -> list[tuple[datetime, datetime]]:
    """All imported period ranges; useful for 'what's been covered?' queries.
    Either or both schema_* filters may be omitted for broader queries."""

# Period-replacement pattern
def delete_import_batch(batch: ImportBatch) -> int:
    """Cascade-deletes batch, its TransactionImport rows, and the linked Transactions.
    Returns count of deleted Transactions. Internally calls core's delete_range
    after computing the batch's transaction set."""

# Metadata-key pattern
def find_by_external_ids(
    account, schema_broker, schema_document_kind, external_ids: Iterable[str],
) -> set[str]:
    """Returns the subset of external_ids that already have TransactionImport rows
    for (account, schema_broker, schema_document_kind). Caller filters their input
    rows by this set before importing."""

# File-level pattern
def is_file_imported(account, file_hash) -> Optional[ImportBatch]:
    """Returns the ImportBatch if a file with this hash was already imported for the account."""
```

### Brokerage ships the high-level import function

```python
def import_transactions(
    rows: Iterable["TransactionDict"],
    *,
    batch: ImportBatch,
    batch_size: int = 1000,
    on_error: Literal["raise", "skip", "collect"] = "raise",
) -> "BulkImportResult":
    """Import rows under the given batch, creating TransactionImport rows linking each
    created Transaction back to the batch. Calls TransactionBuilder.bulk_import internally."""
```

Each input row may include `_import_external_id` and `_import_source_data` keys to set on the corresponding `TransactionImport`. Rows without those keys produce `TransactionImport` rows with blank external_id (still linked to the batch).

### Bulk_import does NOT require a batch

Manual user-entry flows (a host's UI that lets users record a single transaction one-at-a-time) shouldn't need to create an ImportBatch. The core primitive `TransactionBuilder.bulk_import` is batch-agnostic; brokerage's `import_transactions` is the batch-aware wrapper. Hosts choose based on their needs.

### Dedup policy is per-import, not per-account

There is no `Account.default_import_dedup_policy` column. Different schemas for the same account use different strategies (Schwab CSV uses period-discipline; IB Flex uses metadata-key). The strategy is selected by the host's import code at import time based on the registered `ImportSchema`.

If a host wants to record their preferences, that's `Account.metadata` JSON or a separate host-side model. Not in the package's schema.

### Worked example: Schwab CSV import

```python
file_hash = hashlib.sha256(file_bytes).hexdigest()

# File-level safety net
if existing := is_file_imported(account, file_hash):
    return {"status": "already_imported", "batch": existing}

period_start, period_end = parse_schwab_period(file_bytes)
parsed_rows = parse_schwab_rows(file_bytes)

# Period-discipline
if is_period_imported(account, "schwab", "trades", period_start, period_end):
    # Host decides: skip, replace, or surface to user
    return {"status": "period_already_imported"}

batch = ImportBatch.objects.create(
    account=account,
    schema_broker="schwab",
    schema_document_kind="trades",
    schema_format_kind="csv",
    schema_version="2026.01",
    period_start=period_start,
    period_end=period_end,
    file_name=file.name,
    file_hash=file_hash,
    imported_by=request.user,
)

result = import_transactions(parsed_rows, batch=batch)
batch.transaction_count = result.inserted
batch.save()
```

### Worked example: IB Flex import (stable IDs)

```python
batch = ImportBatch.objects.create(
    account=account,
    schema_broker="ib",
    schema_document_kind="flex_query",
    schema_format_kind="xml",
    schema_version="3",
    file_hash=file_hash,
)

ib_ids = [r["metadata"]["ib_execution_id"] for r in parsed_rows]
existing = find_by_external_ids(account, "ib", "flex_query", ib_ids)

new_rows = []
for r in parsed_rows:
    if r["metadata"]["ib_execution_id"] not in existing:
        r["_import_external_id"] = r["metadata"]["ib_execution_id"]
        new_rows.append(r)

import_transactions(new_rows, batch=batch)
```

### Worked example: Period replacement

```python
# User noticed an error in their March import; wants to re-import the period
# (Host UI confirms with the user; calls this only after explicit confirmation)

old_batches = ImportBatch.objects.filter(
    account=account,
    schema_broker="schwab",
    schema_document_kind="trades",
    period_start__lte=date(2024, 3, 1), period_end__gte=date(2024, 3, 31),
)
for batch in old_batches:
    delete_import_batch(batch)

# Now re-import normally
new_batch = ImportBatch.objects.create(...)
import_transactions(parsed_rows, batch=new_batch)
```

## Consequences

**Easier:**

- Adopters get the efficient bulk insertion path for free. The hard parts (batching, trigger handling, cachalot invalidation) are written once in core.
- Import provenance is captured in a standard way. Auditing "where did this transaction come from?" is a single FK traversal.
- Period dedup works without requiring stable IDs from the source. Schwab, Fidelity, and similar CSV-only sources are handled cleanly.
- Stable-ID sources (IB, modern APIs) get clean metadata-key dedup.
- File-level dedup catches the "user uploaded the same file twice" case at the cheapest possible layer.
- Brokerage's import functions reuse core's `bulk_import` primitive — same performance, additional bookkeeping.
- Hosts with manual-entry flows don't pay for batch infrastructure they don't use.
- Hosts can build their own dedup strategies on top of the helpers without forking.

**Harder:**

- Import management lives in the brokerage sub-package. Documentation must clearly explain this — adopters can use the `bulk_import` primitive from `django_assets.core` directly, or use the higher-level batch-aware `import_transactions` from `django_assets.brokerage`.
- Hosts that want import tracking but don't want brokerage's transaction templates have to enable brokerage anyway. The brokerage app is shaped to be enabled with its full surface; selective enabling isn't supported.
- `delete_range` is a footgun. The `confirm=True` brake helps but doesn't eliminate the risk. Documentation must explain its semantics carefully.
- Dedup strategies are not auto-applied; the host's import code must explicitly call the appropriate helper before `bulk_import`. Documented patterns help, but new adopters may skip dedup and create duplicates.

**Deferred:**

- An OCC memo / corporate-action ingestion pipeline. Sibling package per ADR-0011.
- Broker-specific parsers (Schwab CSV → row format, Fidelity QFX → row format, IB Flex XML → row format). Sibling packages or host-built; the dict format `bulk_import` accepts is the standard surface.
- A more sophisticated content-hash dedup helper. Hosts that need it build it on top of `TransactionImport.content_hash`; the package doesn't sanction it because of false-positive risk.
- Per-account default dedup policy. Hosts encode this in their own UI or `Account.metadata` if they want it.

## Related

- ADR-0004 establishes the deferred balance trigger that batched inserts must compose with correctly.
- ADR-0006 establishes CASCADE on user delete; combined with the single-owner Account model, makes whole-transaction DELETE balance-safe (`delete_range` relies on this).
- ADR-0011 establishes that core is the ledger primitive; this ADR's split (core ships primitives, brokerage ships policy) is a direct application.
- ADR-0015 establishes the single-app distribution; this ADR places import management in the brokerage sub-package rather than creating a separate one.
- OQ-13 in `open-questions.md` is resolved by this ADR.
