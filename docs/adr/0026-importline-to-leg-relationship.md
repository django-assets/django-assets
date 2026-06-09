# ADR-0026: ImportLine → TransactionLeg relationship shape

## Status

Accepted — 2026-06-07

## Context

Per ADR-0024 and ADR-0025, broker downloads are stored as `ImportLine` records in `django_assets.brokerage`. Each `ImportLine` carries one row of broker statement data. The reconciliation linkage — which `core.TransactionLeg` (or legs) a given `ImportLine` reconciles — needs to be modeled.

A single broker statement row can reconcile **more than one asset-account leg**. Canonical example: a stock buy CSV row produces a single ledger Transaction with both a cash debit (on `brokerage_cash`) and a holdings credit (on `brokerage_holdings`). Both are broker-reported asset accounts; both should be reconciled by the same source row.

Three structurally different ways to model the line-to-leg relationship were considered:

- **A1**: auto-generated M2M between `ImportLine` and `TransactionLeg` (no Python join model — Django manages the join table behind the scenes).
- **A2**: explicit `ImportLineMatch` through model on the M2M (third Python model carries per-match fields like `role` and `matched_at`).
- **B**: direct FK column on `ImportLine` to `TransactionLeg`; multi-leg CSV rows handled by creating multiple `ImportLine` rows.

This ADR makes that choice and only that choice. All other settled facts about reconciliation (placement in brokerage, scope is asset-account legs only, FK target is `core.TransactionLeg`) remain unchanged.

## Decision

Adopt **A1**: an auto-generated M2M between `ImportLine` and `TransactionLeg`, with no explicit Python join model.

```python
class ImportLine(models.Model):
    batch = models.ForeignKey(ImportBatch, ..., null=True, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    kind = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_legs = models.ManyToManyField(
        "django_assets.TransactionLeg",
        related_name="reconciliation_lines",
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
```

Django auto-creates the join table; no `ImportLineMatch` model in Python. The relationship is **binary**: either an `ImportLine` is in the join for a given `TransactionLeg`, or it isn't. No per-match `role`, no `matched_at`, no `note` on individual matches.

### Rationale

- A reconciled leg is reconciled. The state is intrinsically a flag, not a structured record.
- Per-match metadata (when matched, by whom, in what role) was the only motivator for A2's third model. The user explicitly does not want any of it.
- One CSV row remains one `ImportLine` row (vs. B's duplication), preserving `(batch, line_number)` uniqueness and storing raw evidence once.
- Multi-asset-leg matches (stock buy debiting cash AND crediting holdings) are first-class via the M2M.
- "Is this leg reconciled?" is a single reverse-relationship existence check, queryable from admin and DRF without custom code.

### Query patterns

```python
# Link a leg to an import line (during import or manual match)
line.matched_legs.add(leg)
line.matched_legs.add(cash_leg, holdings_leg)  # multi-leg

# Unflip a single leg (re-opens for editing)
line.matched_legs.remove(leg)

# Unflip everything on this line (returns to fully unmatched)
line.matched_legs.clear()

# "Is this leg reconciled?" — used by the brokerage signal handler
leg.reconciliation_lines.exists()

# "What lines reconcile this leg?"
leg.reconciliation_lines.all()

# "What legs does this line reconcile?"
line.matched_legs.all()

# "All unmatched broker-CSV lines"
ImportLine.objects.filter(
    kind__startswith="broker_",
    matched_legs__isnull=True,
)
```

### What is NOT in the design

- `role` field per match. Inferable from `leg.account` (cash vs. holdings) if a consumer needs it.
- `matched_at` timestamp per match. The `ImportLine.metadata` or the leg's `created_at` is close enough in practice; if a precise audit of "when did this specific match get created" becomes critical later, we revisit.
- A `note` field per match. The `ImportLine.note` field covers the whole row.

If any of these become genuinely necessary later, a migration from auto-generated to explicit through model is non-trivial but possible. The cost of starting simple is recoverable.

## Considered alternatives

### A2: Explicit through model `ImportLineMatch` (considered; rejected)

`ImportLine` carries no FK to `TransactionLeg` directly. Instead a third model (`ImportLineMatch` or similar) joins them many-to-many:

```python
class ImportLine(models.Model):
    batch = models.ForeignKey(ImportBatch, ..., null=True, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    kind = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_legs = models.ManyToManyField(
        "django_assets.TransactionLeg",
        through="ImportLineMatch",
        related_name="reconciliation_lines",
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)


class ImportLineMatch(models.Model):
    """The join between an ImportLine and one reconciled asset-side TransactionLeg."""
    import_line = models.ForeignKey(ImportLine, related_name="matches", on_delete=models.CASCADE)
    leg = models.ForeignKey(
        "django_assets.TransactionLeg",
        related_name="import_line_matches",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=40, blank=True)  # "cash", "holdings", "transferred_in", ...
    matched_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["import_line", "leg"],
                name="uniq_importline_leg_match",
            ),
        ]
```

For the AAPL buy example: one `ImportLine` row + two `ImportLineMatch` rows (one for the cash leg with `role="cash"`, one for the holdings leg with `role="holdings"`).

**Pros**

- One CSV row = one `ImportLine` row. The raw evidence is stored once.
- Multi-leg reconciliation is first-class (M2M is the natural shape).
- Per-match metadata (`role`, `matched_at`, `note`) gives clean admin and report views — "this match represents the cash side, established at 20:01:00Z."
- "Unmatched" is a single test: `ImportLine.matches.empty()` or `ImportLine.objects.filter(matches__isnull=True)`.
- Single ImportLine that goes from fully unmatched → partially matched → fully matched as the importer or user matches each asset leg in turn. Useful for partial-match workflows.
- Easy to extend the through model later (`matched_by_user`, `match_method`, `confidence`, etc.) without touching `ImportLine` or `TransactionLeg`.
- Re-matching (unflip + re-reconcile) deletes/creates `ImportLineMatch` rows without disturbing the `ImportLine` itself.

**Cons**

- Three models (`ImportLine`, `ImportLineMatch`, plus the existing `ImportBatch`).
- M2M with `through` is more complex to set up in admin and DRF serializers than a direct FK.
- Slightly heavier query patterns ("is this leg reconciled?" goes through the join table, not a direct FK lookup).

### B: Direct FK column on `ImportLine` (considered; rejected)

`ImportLine` has a direct nullable FK to `TransactionLeg`. Each `ImportLine` row maps to at most one leg. Multi-leg CSV rows are represented by **multiple `ImportLine` rows sharing the same `batch` and `line_number`**:

```python
class ImportLine(models.Model):
    batch = models.ForeignKey(ImportBatch, ..., null=True, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    kind = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_leg = models.ForeignKey(
        "django_assets.TransactionLeg",
        null=True, blank=True,
        related_name="reconciliation_lines",
        on_delete=models.SET_NULL,
    )
    role = models.CharField(max_length=40, blank=True)  # "cash", "holdings", ...
    matched_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
```

For the AAPL buy example: two `ImportLine` rows, both `line_number=42`, one with `matched_leg=cash_leg, role="cash"` and one with `matched_leg=holdings_leg, role="holdings"`. The same `raw_data` is duplicated across both rows (or stored once and referenced).

**Pros**

- Two models instead of three.
- Direct FK is the simplest possible reverse relationship: `leg.reconciliation_lines.first()` is a single attribute access.
- M2M-with-through complexities (custom through admin, M2M DRF serializers) are avoided.
- "Is this leg reconciled?" is a direct FK reverse-lookup.

**Cons**

- One CSV row → multiple `ImportLine` rows for multi-asset cases. The raw evidence is duplicated, OR stored once with a separate "parent line" model (which reintroduces the third model and defeats the simplification).
- `(batch, line_number)` is no longer unique, breaking the natural-key intuition. Or `line_number` becomes a synthetic sub-key like "42.cash" and "42.holdings", which is ugly.
- "Unmatched" queries get awkward for partial matches: if one `ImportLine` row is matched and a sibling row (same `batch`, same `line_number`) is unmatched, what does "this CSV row is unmatched" mean?
- Adding new per-match metadata fields means adding nullable columns to `ImportLine` itself rather than to a dedicated through model. The `ImportLine` schema accretes responsibilities over time.
- Re-matching workflows have to coordinate across all `ImportLine` rows for the same CSV row.

## Trade-off summary

| | A1 (auto M2M — accepted) | A2 (through M2M) | B (direct FK + sibling rows) |
| --- | --- | --- | --- |
| Python models | 2 (`ImportLine`, `ImportBatch`) | 3 (+ `ImportLineMatch`) | 2 |
| DB tables | 3 (incl. auto-join) | 3 | 2 |
| Multi-leg CSV rows | First-class | First-class | Multiple `ImportLine` rows |
| Raw evidence storage | Once per CSV row | Once per CSV row | Duplicated or split out |
| `(batch, line_number)` unique | Preserved | Preserved | Broken (or synthesized) |
| Per-match metadata | None (intentional) | `role`, `matched_at`, etc. | Nullable columns on `ImportLine` |
| "Is leg reconciled?" | `leg.reconciliation_lines.exists()` | Through `import_line_matches` | `leg.reconciliation_lines.first()` |
| Admin / DRF complexity | Plain M2M | M2M-with-through patterns | Plain FK patterns |

## Consequences

**Easier:**

- Simplest possible schema for the M2M case: two Python models, plus the auto-join that Django manages invisibly.
- `line.matched_legs.add(leg)` / `.remove(leg)` / `.clear()` are the natural API.
- "Is reconciled" is a single reverse-existence check — clean in admin and DRF.
- Forward-compatible: if per-match metadata ever becomes essential, migrating to an explicit through model is possible (with some effort).

**Harder:**

- No structured per-match information. If reporting needs to distinguish "which side of the CSV row matched which leg," it has to infer from the leg's `account` (e.g., "is this an asset-cash account or an asset-holdings account?").
- Migrating from auto-generated to explicit through later is non-trivial. Doing it well requires careful schema management. We accept this cost because the current need for per-match metadata is zero.

## Related

- ADR-0024 (Reconciliation scope) — establishes the placement (brokerage) and target (`core.TransactionLeg`); this ADR is one of the implementation details deferred from there.
- ADR-0025 (Broker download lines — storage and matching workflow) — frames the broader `ImportLine` design; this ADR resolves one of its sub-questions (asset-side cardinality).
- ADR-0019 (Bulk import primitives; import management) — establishes the broader import machinery that `ImportLine` slots into.
