# ADR-0011: Core does not track corporate actions, ingest broker feeds, or own external reality

## Status

Accepted — 2026-06-03

## Context

The broader principle that core ships only numeric integrity is established in ADR-0020. This ADR is a corollary, scoped specifically to the question that surfaced first during design review: should core be responsible for tracking corporate actions (splits, dividends, spinoffs, mergers), ingesting broker feeds, or owning a canonical view of "what happened to AAPL on date X"?

Two realistic user paths drive the answer:

1. **Broker-statement import.** A user uploads a CSV from their brokerage. The brokerage has already applied every corporate action — when AAPL split 4:1, the user's downloaded transaction history shows a "+300 AAPL split adjustment" row, not "AAPL split 4:1, please figure it out." The user (or the import adapter) records that row as a balanced transaction. The ledger just verifies that the legs balance.
2. **Backtest against historical reality.** A user simulates "what if I had bought 100 AAPL on 2020-01-01 and held until today." The simulator generates trades and, on cutover dates, applies splits and dividends. This path requires accurate historical corporate-action data — otherwise the simulated portfolio diverges from reality.

The first path needs no corporate-action machinery in core. The second path needs corporate-action data somewhere, but that data is operationally expensive to maintain (the OCC publishes roughly five adjustment memos per business day; international markets multiply the burden; crypto airdrops and forks add a different category of complexity) and varies wildly across adopters.

Combining both observations: the corporate-actions story belongs in optional sibling sub-packages and host implementations. Core is correct for path 1 without help; path 2's data is populated by whoever takes on that scope.

## Decision

### Core does not commit to any of the following

1. **OCC memo ingestion.** Parsing OCC adjustment memos and turning them into structured records is not core's responsibility.
2. **Broker-feed parsers.** Schwab CSV, Fidelity QFX, Interactive Brokers Flex, Plaid investment transactions — the package does not ship parsers for any of these. Hosts and sibling packages build their own import adapters that produce balanced ledger transactions.
3. **Exchange-notice ingestion.** Symbol changes, exchange transfers, delistings — not core's job to track.
4. **Universal correctness about "what happened to AAPL on date X."** Core makes no claim about the completeness or accuracy of corporate-action data. That is a host-level commitment, made by hosts (or sibling packages) that choose to take it on.
5. **Automated fan-out across users.** When a user records a split for their own AAPL position, the package generates one transaction for that user. The package does NOT iterate across all users holding AAPL and apply splits to everyone — that would require a single source of truth for corporate actions, which is exactly what core declines to be.
6. **Per-instrument issuer pools or system accounts.** Issuer-style accounts (per-instrument supply tracking, cross-user pools) are not built into core. Hosts that want this granularity create the accounts themselves and route legs through them by convention.

### What this means for path 1 (broker import)

Core handles it fully without help from any other app. The broker's pre-adjusted transaction rows go through `TransactionBuilder.bulk_import` (per ADR-0019); the deferred balance trigger (per ADR-0004) validates per-instrument zero-sum. No corporate-action records are needed.

`django_assets.brokerage` provides the import-management layer (ImportBatch, dedup helpers) but still does not require corporate-action data.

### What this means for path 2 (backtest)

Backtest engines need historical corporate-action data. That data lives in:

- A future sibling package (e.g., `django-assets-corp-actions` or `django-assets-occ-feed`), or
- The host's own ingestion pipeline, or
- An external data feed the host integrates with.

When a host runs a backtest that needs to apply a split, that's a separate Transaction the backtest engine inserts via the same `bulk_import` mechanism. The ledger doesn't know it's a "split" — it's just a balanced transaction with whatever metadata the engine chooses to attach.

### Deferred (explicitly out of scope for the `django-assets` distribution as a whole)

- OCC adjustment memo parsing.
- Broker statement adapters (Schwab, Fidelity, IB, Robinhood, Plaid, etc.).
- Symbol normalization across data feeds (Bloomberg, Reuters, Polygon, Tradier).
- Cross-user corporate-action fan-out.

These belong to integration packages or host applications. If any becomes broadly demanded, it can be addressed in a separate PyPI package without changing the core distribution.

## Consequences

**Easier:**

- The primary target host can ship a portfolio app on top of this package without committing to corporate-action tracking. Users import broker statements and get correct balanced ledgers immediately.
- Future expansion (a host eventually builds a central corporate-actions DB for backtest) does not require changing the package — it requires populating new tables in a sibling sub-package or host code. The same ledger primitives work in both directions.
- The maintenance burden of the package is contained. The package authors are not on the hook for OCC memo coverage, broker-feed quirks, or exchange-notice timeliness.
- Adopters with different scope ambitions (institutional, retail, academic) all consume the same package and choose how much external-reality data they want to populate.

**Harder:**

- Documentation must clearly distinguish "the ledger works" from "this analytic is meaningful." Asking `Portfolio.at(account, as_of)` always returns a correct aggregation of recorded transactions; asking it to "reflect a corporate action that wasn't recorded" is impossible by construction. Hosts must communicate this distinction to their own users.
- Hosts that want backtesting AND don't want to build their own corporate-actions ingestion are stuck without that feature until either they build it or a sibling package ships it.

## Related

- ADR-0020 (Core ships only numeric integrity) — the broader principle this ADR is a corollary of.
- ADR-0019 (Bulk import primitives in core; import management in brokerage) — the mechanism by which broker-statement imports work.
- ADR-0004 (DDL install hybrid) — the integrity machinery the ledger relies on.
