# ADR-0007: Portfolio is a query class, not a stored entity

## Status

Accepted — 2026-06-02

## Context

The original README and supporting docs reference `Portfolio` in several places:

- `Portfolio.at(account, as_of)` — referenced as a class method that returns a holdings snapshot.
- "Time-travel portfolios" — listed as a core capability of the system.
- Roadmap entry "v0.2: Query APIs (`Portfolio.at`, `Holding.current`)" — classifies `Portfolio` alongside `Holding` as a query API.

Across all docs, every example of `Portfolio` is as a callable returning a dict-like `{instrument: quantity}`, computed from `TransactionLeg` rows. The README's core data model table lists `exchanges`, `instruments`, `identifiers`, `accounts`, `transactions`, `transaction_legs`, and `holdings` — but no `portfolios` table. The extension-pattern docs describe "Portfolio models that aggregate accounts" as a use case adopters might build in their own apps, not something the core ships.

The question came up during design review whether `Portfolio` should be promoted to a first-class stored entity:

- **Stored entity**: `Portfolio` becomes a Django model with FKs from `Account` (`User -> Portfolio -> Account -> Transaction`), or a many-to-many grouping (`Portfolio.accounts = M2M`). Useful when adopters want to attach metadata to portfolios, share grouping rules, or model portfolios as cross-account aggregations.
- **Query class only**: `Portfolio` stays a computed view. Adopters who need a stored portfolio entity build it in their own application with FKs into the core `Account` table.

The implications differ significantly:

- Promoting `Portfolio` to an entity requires a new schema, FKs throughout, and rewrites of every `Portfolio.at(account, as_of)` example to consider grouping logic.
- Keeping `Portfolio` as a query class preserves the simpler `User -> Account -> Transaction -> TransactionLeg` chain and lets adopters layer their own portfolio model on top without forcing one shape on everyone.

## Decision

`Portfolio` remains a query/aggregation class. The package does not ship a `Portfolio` Django model. There is no `portfolios` table, no `Portfolio` row, and no FK from `Account`, `Transaction`, or `TransactionLeg` to a Portfolio.

`Portfolio.at(account, as_of=None)` returns a computed snapshot (dict-like `{Instrument: Decimal}`) by aggregating `TransactionLeg` rows for the given Account up to `as_of`. It is implemented as a class with a class method, not a Django model.

Adopters who need a stored portfolio entity build it in their own application:

```python
# in host app
class Portfolio(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    accounts = models.ManyToManyField("django_assets.Account")

    def at(self, as_of=None):
        from django_assets.core import Portfolio as CorePortfolio
        snapshots = [CorePortfolio.at(acc, as_of) for acc in self.accounts.all()]
        return merge_snapshots(snapshots)
```

This pattern is documented as the recommended approach in the extension patterns guide.

## Consequences

**Easier:**

- The schema stays small: six tables, not seven.
- Every adopter is free to choose whether their portfolio is single-account, multi-account, user-scoped, organization-scoped, tag-based, or strategy-based. The core doesn't pick a model that constrains their choice.
- The `Portfolio.at(account, as_of)` query API is straightforward — single Account, one snapshot.
- Holdings derivation is unambiguous: there is exactly one source of truth (`TransactionLeg`), and one aggregation level (Account).

**Harder:**

- Adopters who want a stored, queryable, sharable "Portfolio" entity must build it themselves. The package documents how, but the work is theirs.
- Cross-account snapshots (e.g., "all my retirement accounts") require the adopter to compose multiple `Portfolio.at(account, as_of)` calls and merge results. The package may eventually ship a `MultiAccountPortfolio.at(accounts, as_of)` helper, but it would aggregate the same primitives, not introduce a new stored entity.
- Naming collision risk: adopter-built `Portfolio` model has the same name as the core's `Portfolio` query class. Documentation must guide adopters to name their model differently (`UserPortfolio`, `PortfolioGroup`) or import the core's `Portfolio` with an alias.

## Related

- The Portfolio Holdings Query example in [django_assets_core_extension_pattern_4_querying_reporting.md](../django_assets_core_extension_pattern_4_querying_reporting.md) shows the pattern.
- ADR-0005 establishes single-owner Accounts. Combined with this ADR, the package commits to "one user, many accounts, no first-class portfolio object — adopters compose what they need."
