# ADR-0016: Holdings via live aggregation; no Holding table; minimal indexes

## Status

Accepted — 2026-06-02

## Context

The ledger needs an API for "what does this account hold in this instrument right now?" and "what did this account hold at this point in time?" The straightforward implementation is to aggregate `TransactionLeg.amount` over the relevant transactions. The question is whether to materialize that aggregation into stored state (a `Holding` table, a materialized view, or denormalized balance columns) or compute it live on every query.

Three implementation shapes were considered:

- **Live aggregation.** `Holding.current(account, instrument)` runs `SELECT SUM(amount) FROM transaction_leg WHERE ...` every time. Always correct by construction; performance depends on indexes and on the number of legs being scanned.
- **Stored `Holding` Django model.** A denormalized row per `(account, instrument)` carrying the running balance, maintained by signals or triggers. Fast reads but introduces a parallel source of truth that can drift if any write path bypasses the maintenance hook.
- **Postgres materialized view.** Stores the aggregation result; refreshed on a schedule via a Celery task, management command, or `pg_cron`. Fast reads, eventual consistency, no per-write maintenance code, but requires the host to wire a refresh trigger.

The materialized view requires *someone* to drive refreshes — Postgres does not auto-refresh materialized views. That makes it operationally heavier than the "just compute it" approach for early-stage development, where transaction volumes are in the low thousands and live aggregation is sub-millisecond.

Performance projections for live aggregation against a properly indexed `TransactionLeg` table:

| Account size (transactions) | Approximate `Portfolio.at` latency |
| --- | --- |
| 100 | < 1 ms |
| 1,000 | 1–3 ms |
| 10,000 | 5–20 ms |
| 100,000 | 50–200 ms |
| 1,000,000+ | 500 ms+ |

For the target retail audience and the development phase, live aggregation is fast enough that any optimization beyond required indexes is premature. Indexes can be added later via `CREATE INDEX CONCURRENTLY` without locking the table; the materialized view can be added as a future non-breaking schema change if any deployment grows past the latency point where it matters. Neither path requires advance commitment in v0.1.

## Decision

### Holding is a value class, not a Django model

```python
class Holding:
    """Computed snapshot of an account's position in one instrument.
    Not a Django model — no `holdings` table exists."""

    @classmethod
    def current(cls, account, instrument) -> Decimal:
        """Sum of all TransactionLeg.amount for (account, instrument), at the present moment."""
        ...

    @classmethod
    def historical(cls, account, instrument, as_of) -> Decimal:
        """Sum filtered to transaction.timestamp <= as_of (per ADR-0012's settlement-time semantics)."""
        ...


class Portfolio:
    @classmethod
    def at(cls, account, as_of=None) -> dict[Instrument, Decimal]:
        """All non-zero holdings for the account at as_of.
        Implemented as a single GROUP BY query against TransactionLeg."""
        ...
```

There is no `holdings` table in v0.1. There is no `Holding` Django model class. `Holding` and `Portfolio` are pure value-returning classes with classmethods that run ORM queries.

### Required v0.1 indexes only

Two index categories are mandatory for v0.1:

1. **Django automatic FK indexes.** Django creates a single-column index on every `ForeignKey` field by default. This covers `transaction.account_id`, `transaction_leg.transaction_id`, `transaction_leg.instrument_id`, and so on with no additional DDL.
2. **The composite `transaction_leg(transaction_id, instrument_id)` index.** Required by the deferred balance trigger from ADR-0004 — without it, the trigger's `GROUP BY instrument_id` per transaction would degrade to a sequential scan. Shipped as part of the core required DDL.

Nothing else is required. Specifically:

- No composite `transaction(account_id, timestamp)` index in v0.1.
- No covering index (`INCLUDE (amount)`) in v0.1.
- No partitioning on `transaction_leg` in v0.1 (already deferred to v0.3+ per the README roadmap).
- No materialized view in v0.1.

### Optimization is a later, non-blocking concern

When profiling identifies a slow `Portfolio.at` or `Holding.current` query in a real deployment, optimizations are added incrementally without breaking changes:

- **Composite `(account_id, timestamp)` index on `transaction`** — added via `CREATE INDEX CONCURRENTLY` in a migration; no app code changes; query planner adopts it automatically.
- **Covering index `transaction_leg(transaction_id, instrument_id) INCLUDE (amount)`** — same. Lets the aggregation be answered by an index-only scan without heap fetches; reduces latency at scale by avoiding random I/O.
- **Denormalized `account_id` on `transaction_leg`** — schema migration adds the column with a default; backfill populates it; future queries skip the JOIN entirely. More invasive but available if needed.
- **Materialized view backing `Holding.current` and `Portfolio.at`** — adds an optional view shipped in a future minor version, refreshed by a Celery task or management command (Postgres does not auto-refresh materialized views; the host wires the schedule). The view is part of the optional schema per ADR-0011, not required.
- **Range partitioning on `transaction_leg` by month** — already on the v0.3 roadmap. Improves both write throughput at scale and read pruning for time-bounded queries.

None of these are committed for v0.1. None of them require advance commitment to keep the upgrade path clean.

### What this means for the public API

The `Holding.current` / `Portfolio.at` API contract does not change across the v0.1 → future-optimization line. If a materialized view ships in a later version, `Holding.current` reads from it transparently; callers see the same function with the same return type. The decision here is purely about implementation strategy and shipped indexes.

There is no `Holding.live()` vs `Holding.cached()` distinction — live aggregation is the only mode. If a future ADR introduces a materialized view, it can either:

- Replace the live aggregation transparently (callers see the same API, slightly stale reads), or
- Add a second method (`Holding.cached(...)`) for callers that want explicit cache-vs-live control.

That decision is deferred until materialized view becomes a real need.

## Consequences

**Easier:**

- Schema is minimal — no `holdings` table, no parallel source of truth, no maintenance hooks to write or test.
- Integrity is automatic — there is no denormalized balance to drift from reality.
- v0.1 ships faster because the optimization story is documented but not built.
- Indexes added later via `CREATE INDEX CONCURRENTLY` cause no downtime and no breaking changes.
- The query path is one ORM call with one indexed JOIN. Easy to debug, easy to explain, easy to test.

**Harder:**

- Performance ceiling is lower than it could be. Accounts with hundreds of thousands of transactions will see noticeable latency on `Portfolio.at`. Documented as an expected outcome with a known remediation path.
- Pre-transaction checks ("do I have enough cash for this trade?") run the full aggregation rather than reading a cached balance. For high-frequency transaction creation, this may become a hot path. Documented; optimization path available.
- Some adopters may expect a `Holding` model they can FK to. Documentation must clarify that holdings are computed; if they need a persistent handle (e.g., for tagging or notes attached to a position), they create their own model in their host app.

**Deferred (with documented upgrade paths):**

- Composite `(account_id, timestamp)` index on `transaction`.
- Covering index on `transaction_leg`.
- Denormalized `account_id` on `transaction_leg`.
- Materialized view backing `Holding.current` / `Portfolio.at`.
- Range partitioning on `transaction_leg`.

## Related

- ADR-0004 establishes the deferred balance trigger; the `transaction_leg(transaction_id, instrument_id)` index this ADR requires is what the trigger needs.
- ADR-0007 establishes that `Portfolio` is a query class, not a stored entity. This ADR carries the same shape forward to `Holding`.
- ADR-0011 establishes that the core is the ledger and that optional schema (materialized views, etc.) is host-driven. The future materialized view, if it ships, falls in the optional-schema bucket.
- ADR-0012 establishes the settlement vs trade timestamp model; `Holding.current` and `Portfolio.at` both use the settlement `timestamp` per that ADR.
- OQ-10 in `open-questions.md` is resolved by this ADR.
