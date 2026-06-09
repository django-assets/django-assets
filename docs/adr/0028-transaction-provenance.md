# ADR-0028: Transaction provenance — origin marker and import dedup matching

## Status

Proposed — 2026-06-07

## Context

ADR-0024 established reconciliation as asset-account-leg-only via `ImportLine.matched_legs`. ADR-0027 established that imports go through `ImportSchema.parse_batch` → `materialize_line`, with an orchestrator handling persistence and dedup. The orchestrator's `find_matching_legs(line)` helper was named but explicitly deferred.

What none of those settled: how do we distinguish a Transaction that was born as a manual user entry from one that was created by an importer? And, given that distinction, how does the import orchestrator find existing unreconciled manual entries that should be matched against a newly-arrived broker line, rather than creating duplicate Transactions?

### The central use case

1. User manually enters "Bought 100 AAPL @ $150 on 2026-06-01" via UI or a host application. Transaction is created in the ledger.
2. Two weeks later, user imports a Fidelity Trades CSV that contains that same 2026-06-01 AAPL purchase as one of its rows.
3. **Without dedup**: importer creates a SECOND Transaction for the same purchase. User now has two AAPL purchases in their ledger. Cash is double-debited; holdings show 200 shares instead of 100. The ledger is corrupted in a way that's hard to detect after the fact.
4. **With dedup**: importer recognizes that the parsed broker row corresponds to an existing unreconciled manual Transaction. It does not create a new Transaction. Instead, it adds the existing manual Transaction's asset legs to `ImportLine.matched_legs`. The user's manual entry now has broker corroboration. One Transaction, one source of truth.

The orchestrator needs two pieces to do this:

1. **A way to identify candidate Transactions.** Which Transactions are "manually entered, not yet reconciled"? Without a provenance marker on the Transaction itself, this is unanswerable — reconciliation state alone doesn't distinguish "I made this up" from "the importer made this and somehow lost its line."
2. **A matching algorithm.** Given a parsed broker row and a population of candidate Transactions, find the existing leg (if any) that corresponds.

This ADR settles both.

## Decision

### Part 1: `Transaction.origin` field in core

Add a `Transaction.origin` field on the core `Transaction` model:

```python
class Transaction(models.Model):
    # ... existing core fields ...
    origin = models.CharField(
        max_length=20,
        default="manual",
    )
```

Conventional values:

| Value | Meaning |
| --- | --- |
| `"manual"` | Created by a user action (UI form submit, admin, host code call). Default. |
| `"import"` | Created by `ImportSchema.materialize_line` during a batch import. |

The field is a `CharField`, not a strict `choices`-bounded enum, so hosts can register additional values (`"scheduled"`, `"api_external"`, `"corporate_action"`, etc.) without modifying the core schema. ADR-0028 only specifies the two core values; everything else is host-extensible convention.

**Origin is in core, not brokerage.** Reasoning:

- Provenance is universal metadata. Hosts that never install the brokerage app still benefit — all their Transactions are `origin="manual"` by default; the field carries useful audit information at no cost.
- Putting it in brokerage would require a related `TransactionProvenance` model joined to core's `Transaction`, adding a query indirection on every dedup check, every admin display, every report.
- ADR-0020 (core ships only numeric integrity) is preserved: core *stores* the label but does not *enforce or react* to it. Origin drives no core behavior. The same logic that already permits `Transaction.notes`, `Transaction.created_at`, and similar metadata in core applies here.

**Origin is immutable after creation.** It records HOW the Transaction came into being, not its current state. A manual Transaction that is later matched to a broker import retains `origin="manual"` — the reconciliation state (via `matched_legs`) is the orthogonal "is it reconciled now" axis.

### Part 2: Dedup matching during import

The orchestrator's `find_matching_legs(line)` (from ADR-0027) works as follows:

```python
def find_matching_legs(line: ImportLine) -> list[TransactionLeg] | MatchAmbiguous:
    """Find existing unreconciled manual asset legs matching this line.

    Returns:
      []                    no candidates — line will materialize a new Transaction
      [leg, ...]            one candidate Transaction — return its unreconciled asset legs
      MatchAmbiguous        multiple candidate Transactions — defer to user review
    """
    schema = line.batch.get_schema()
    criteria = schema.match_criteria(line)

    candidates = Transaction.objects.filter(
        origin="manual",
        transaction_date__range=(
            criteria.date - timedelta(days=criteria.date_window_days),
            criteria.date + timedelta(days=criteria.date_window_days),
        ),
        legs__account=line.batch.account,
        legs__account__is_broker_asset=True,
        legs__instrument=criteria.instrument,
        legs__amount=criteria.amount,
        legs__reconciliation_lines__isnull=True,
    ).distinct()

    count = candidates.count()
    if count == 0:
        return []
    if count > 1:
        return MatchAmbiguous(candidates=list(candidates))

    transaction = candidates.first()
    return list(
        transaction.legs.filter(
            account__is_broker_asset=True,
            reconciliation_lines__isnull=True,
        )
    )
```

The schema is responsible for translating a parsed `ImportLine` into a typed `MatchCriteria`. The orchestrator is responsible for running the query.

### Part 3: `ImportSchema.match_criteria`

`ImportSchema` gains a third required method:

```python
class ImportSchema:
    # ... parse_batch, materialize_line (per ADR-0027) ...

    def match_criteria(self, line) -> MatchCriteria:
        """Extract the fields used to find a matching pre-existing manual leg.

        Returns a MatchCriteria describing how the orchestrator should search
        for a candidate Transaction in the ledger.

        Schemas override this for broker-specific quirks: settlement-date
        offsets, special date-window handling, or brokers where the "amount"
        in the row needs unit conversion before comparing to ledger amounts.
        """
        raise NotImplementedError
```

```python
@dataclass(frozen=True)
class MatchCriteria:
    date: date                     # the trade date as the broker reports it
    instrument: Instrument         # the asset traded (or USD for cash-only events)
    amount: Decimal                # the asset-side amount (positive or negative)
    date_window_days: int = 2      # tolerance for trade-date vs settlement-date drift
```

The default `date_window_days=2` handles the common T+1/T+2 settlement-date case. Brokers with longer reporting delays override.

### Part 4: Orchestrator outcomes

| Result of `find_matching_legs(line)` | Orchestrator action |
| --- | --- |
| `[]` (no candidates) | Call `schema.materialize_line(line)`. New Transaction created with `origin="import"`. Asset legs added to `line.matched_legs`. |
| `[leg, ...]` (one candidate Transaction) | Add the returned legs to `line.matched_legs`. **Do not call `materialize_line`.** Existing Transaction stays `origin="manual"`. Broker evidence now corroborates the manual entry. |
| `MatchAmbiguous` (multiple candidates) | Skip the line for now. The orchestrator surfaces it (admin or DRF endpoint per ADR-0025) for user review. The user picks the right match — or chooses "none match — create new." |

### What is NOT in the design

- **Augmenting matched manual Transactions with broker-extracted detail.** When the importer matches an existing manual Transaction, it does not modify that Transaction to add the broker's commission/fee/counterparty legs that would have been included if a fresh Transaction had been materialized. The user gets the asset-leg reconciliation; if they want the broker's full detail, they edit the Transaction manually. (Editing reconciled asset legs is blocked per ADR-0024; editing the non-asset legs that the manual entry already has is fine.)

- **Leg-level origin.** Origin is at the Transaction level. The minor case of "user edits a commission leg post-import" doesn't change the Transaction's origin — the Transaction was still born as an import.

- **Origin-based access control.** Core does not enforce "only the importer may create import-origin Transactions" or anything similar. Hosts that want stricter rules can add their own checks via signals.

- **A formal extension registry for origin values.** Hosts extending the set of allowed values (e.g., adding `"scheduled"`) do so by convention; the field is just a CharField. If conflicts between hosts emerge, a future ADR can add a registry.

## Reconciliation strategy adjustments

**None required.** The origin marker is orthogonal to the reconciliation state defined by ADR-0024:

| | reconciled = no (no `matched_legs` entry) | reconciled = yes (has `matched_legs` entry) |
| --- | --- | --- |
| **origin = manual** | User entry, no broker confirmation yet | User entry, broker import has corroborated it |
| **origin = import** | *Should not occur in practice* | Auto-imported, matched to its source line |

The two axes describe a Transaction independently:

- **Matching a manual leg to an import line**: `line.matched_legs.add(manual_leg)`. The leg moves from reconciled=no to reconciled=yes. Origin stays `"manual"`. Standard ADR-0024 mechanics, no new operation.
- **Unflipping a matched manual leg**: `line.matched_legs.remove(manual_leg)`. The leg returns to reconciled=no. The underlying Transaction still has `origin="manual"`. Standard ADR-0024 unflip.
- **Matching a fresh import line**: standard ADR-0027 materialize path. `origin="import"` Transaction created; asset legs added to `matched_legs` immediately.

The `(origin="import", reconciled=no)` cell is empty in practice — an importer always self-reconciles the lines it creates. A loud assertion at the end of `process_batch` enforces this:

```python
def process_batch(batch, source):
    # ... per ADR-0027 ...
    orphans = Transaction.objects.filter(
        origin="import",
        legs__account__is_broker_asset=True,
        legs__reconciliation_lines__isnull=True,
        # ... scoped to this batch ...
    )
    if orphans.exists():
        raise InvariantViolation(
            f"Import created Transactions whose asset legs are not in matched_legs: {orphans!r}"
        )
```

This catches bugs in `materialize_line` implementations that forget to wire `matched_legs`.

## Considered alternatives

### Alt A: Origin lives in brokerage, not core

A `TransactionProvenance` model in brokerage with a one-to-one FK to core's `Transaction`. Brokerage queries through the join to find candidates.

**Pros:** Strictest reading of ADR-0020.
**Cons:** Indirection on every dedup check, every admin display, every report that distinguishes manual from import. Hosts not using brokerage have no way to track origin at all. The provenance concept is universally useful, not brokerage-specific. **Rejected.**

### Alt B: Origin inferred from reconciliation state

A leg with no `reconciliation_lines` = manual; a leg with `reconciliation_lines` = imported.

**Pros:** No new field.
**Cons:** After a manual entry is matched to a late-arriving broker import, the leg HAS reconciliation lines — but the Transaction was still born manually. Provenance and reconciliation are independent axes. Conflating them loses information. **Rejected.**

### Alt C: Per-leg origin

Store origin on each `TransactionLeg`, not on `Transaction`.

**Pros:** Granular.
**Cons:** Origin is a property of the event, not of the leg. A Transaction's legs all came into being at the same time, from the same source. Per-leg origin adds storage cost without information gain. **Rejected.**

### Alt D: No automatic matching — user always confirms

The orchestrator never auto-matches. All matches go through a user-confirmation UI.

**Pros:** Zero risk of false positives.
**Cons:** For the common case (user enters today's trades by hand, imports next week's CSV), the user clicks "yes match" hundreds of times for unambiguous matches. Defeats the purpose of automation. **Rejected.** Multiple-candidate fallback to user review is preserved (see "Orchestrator outcomes").

### Alt E: Matching by Transaction-level hash

The schema computes a stable hash from `(date, instrument, amount, side, account)` and the orchestrator compares hashes.

**Pros:** Single index lookup, very fast.
**Cons:** Hashes can't express the date window (T+1/T+2 settlement). And, harder to debug when a match unexpectedly fails or succeeds — hashes are opaque. The query-based approach is simpler and more inspectable. **Rejected.**

## Consequences

**Easier:**

- Manual entries are first-class citizens. Users can enter today's trades by hand and import next week's CSV without creating duplicates.
- One source of truth per event: a single Transaction, with both its manual origin and its broker corroboration visible.
- "I entered this" vs. "the importer caught this" is queryable, displayable in admin, and audit-useful.
- Schemas declare match criteria; the orchestrator runs the query. Each layer is testable in isolation.
- Date-window matching naturally handles trade-date vs. settlement-date drift without per-broker special casing for the common cases.
- The `(import, unreconciled)` invariant is checkable at end-of-batch — catches buggy `materialize_line` implementations early.

**Harder:**

- A new field in core (`Transaction.origin`). Small but a real schema addition. Mitigated by a sensible default that makes it invisible to hosts not doing imports.
- `match_criteria` logic in every schema. For most schemas the default reasonable implementation is straightforward; brokers with unusual conventions need overrides.
- Multiple-candidate review UI/admin surface required. ADR-0025 already commits to admin + DRF surfaces in brokerage; the ambiguity case extends that surface.
- The origin CharField is open-ended (not strict `choices`). Hosts using different values for the same concept (`"scheduled"` vs. `"recurring"` for the same thing) is a soft consistency issue that needs convention.
- Edge case: a user enters a manual transaction with the wrong date (off by more than `date_window_days`). The importer creates a duplicate. The user has to spot it, delete the manual one, and let the import stand. Same failure mode would exist without ADR-0028, but this design makes it clearer who is responsible (the user picked a date outside the window).

## Related

- ADR-0020 (Core ships only numeric integrity) — `Transaction.origin` is metadata in core, not semantic enforcement.
- ADR-0024 (Reconciliation scope) — origin is orthogonal to leg-level reconciliation state; matching a manual leg uses standard `matched_legs` mechanics.
- ADR-0025 (Broker download lines) — the multi-candidate ambiguity surface extends the admin + DRF surface this ADR specifies.
- ADR-0026 (`ImportLine` → `TransactionLeg`) — the M2M operations used here are unchanged.
- ADR-0027 (Broker import schemas) — `match_criteria` is the third required schema method; the orchestrator's `find_matching_legs` is specified here.
