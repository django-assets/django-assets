# ADR-0021: Brokerage templates follow the source's transaction shape

## Status

Accepted — 2026-06-03

## Context

When `django_assets.brokerage`'s transaction templates record a buy, sell, dividend, exercise, or any other event that involves both an asset/cash flow AND a fee or commission, the question is whether to record:

- **One atomic Transaction** containing all the legs (the trade plus the fee components), or
- **Multiple Transactions** (one for the trade, separate ones for each fee or commission).

This needs a clear default but also needs to handle the legitimate cases where fees are not tied to a trade — annual ADR custody fees, account maintenance fees, wire fees, monthly account-level charges, etc.

The principle that resolves the question: **follow the source's structure.** If the broker reports a trade and its commission as a single statement entry with one net cash effect, the package records it as one atomic Transaction. If the broker posts a fee as its own line item with its own date and no trade attached, the package records it as its own Transaction. The shape of the source data dictates the shape of the recorded transaction.

This is consistent with the Inviolability Rule from ADR-0020 — core stores source-of-truth facts. The "shape" of the transaction (one event or two) is itself part of that ground truth.

## Decision

### Default: one atomic Transaction per source-of-truth event

When the broker reports a trade with its commission, regulatory fees, and any other charges as a single statement entry with one net cash effect on the user's account, the brokerage template emits one `Transaction` whose legs include:

- The asset-side legs (shares/options/etc. in and out).
- The cash-side legs decomposed into separate flows for principal, commission, regulatory fees, and any other components — each routed to the appropriate user-owned tracking account (`user_commissions_paid`, `user_fees_paid`, etc.) per the multi-leg routing convention in ADR-0020.
- The counterparty-side legs.

The per-instrument balance trigger (ADR-0004) validates the whole event atomically.

Example (the HIMS sell from ADR-0020):

```python
sell_option(
    account=user_brokerage,
    option=hims_call,
    contracts=2,
    price=Decimal("7.85"),
    commission=Decimal("0.90"),
    industry_fee=Decimal("0.06"),
)
```

Produces a single Transaction with the multi-leg structure documented in ADR-0020's worked example. The user sees one event in their ledger: "Sold 2 HIMS calls."

### Exception: standalone fee events get their own Transactions

When the broker posts a fee that is not tied to a buy/sell or other primary event, the brokerage template emits a separate Transaction. Common cases:

- Annual ADR custody fees.
- Monthly or annual account maintenance fees.
- Wire transfer fees.
- Inactivity fees.
- Foreign custody fees.
- IRA custodial fees.

These are recorded by their own dedicated templates (e.g., `account_fee`, `adr_fee_deducted`, `wire_fee`, `inactivity_fee`) and produce a Transaction whose legs are just the fee flow itself:

```python
adr_fee_deducted(account=user_cash, instrument=AAPL, amount=Decimal("0.02"))
```

Generates:

```
-$0.02 USD from user_cash
+$0.02 USD to user_adr_fees_paid
```

(Plus optional counterparty-side legs depending on the host's account convention.)

Per-instrument balance trigger validates. The user sees the standalone fee in their ledger as its own event.

### The rule

Follow the broker's source structure:

- **Same statement entry with one net cash effect → one Transaction.** Multi-leg breakdown captures the components per ADR-0020's tracking-account convention.
- **Separate statement entry with its own date → separate Transaction.** Even if the fee is conceptually related to other activity, if the source records it independently, the ledger records it independently.

This stays faithful to ground truth. It also makes broker-statement reconciliation straightforward — each entry in the broker's statement maps to one Transaction in the ledger.

### When the host's source data doesn't disambiguate

Some imports lose the entry-vs-statement distinction (e.g., a CSV that lists "AAPL buy" and "AAPL commission" as separate rows even though the broker really treated them as one event). In these cases the host's import adapter chooses:

- **Best-effort merging**: if rows share a date, instrument, and seem to belong together, merge into one Transaction.
- **Conservative separation**: keep them as separate Transactions and accept that the ledger structure is slightly more granular than the source's logical shape.

Documentation recommends the conservative approach as the safer default. The cost is just slightly more granular ledger entries; correctness is preserved either way.

### Callers

Brokerage templates are atomic ledger constructors. Anything that creates a `Transaction` should go through a template rather than constructing legs by hand. Two principal callers:

- **The import path**: `ImportSchema.materialize_line` (per ADR-0027) calls one or more templates to turn a parsed broker row into ledger Transactions. The schema is what knows "row 3 means a SELL with this commission"; the template is what knows "a SELL is shaped like *this* atomic Transaction."
- **Host application code**: manual entries from the host's UI, scheduled events, and any non-import flow call templates directly.

Templates themselves are agnostic to who is calling them.

## Consequences

**Easier:**

- The user's ledger view mirrors their broker statement view. Reconciliation is intuitive.
- Same-event multi-leg breakdown captures all the source's components (principal, commission, regulatory fees) atomically per the Inviolability Rule from ADR-0020.
- Standalone fees are not awkwardly attached to unrelated trades; they're their own events with their own dates and audit trails.
- The brokerage templates have a clean rule for when to bundle and when to separate.

**Harder:**

- Hosts importing from sources that don't distinguish entry shape (some CSV exports) have to make a judgment call. Documentation guides toward conservative separation.
- The convention vocabulary (account naming for `user_commissions_paid`, `user_adr_fees_paid`, etc.) is documented in brokerage but is ultimately host-extensible. Different hosts may name accounts differently.

**Deferred:**

- A "merge close-in-time fee adjustments into the original trade" helper for sources that report them separately. Not in v0.1; hosts can post their own offset transactions if they want this level of cleanup.

## Related

- ADR-0020 (Core ships only numeric integrity) — establishes the Inviolability Rule and the multi-leg fee-routing pattern this ADR specifies for templates.
- ADR-0004 (DDL install hybrid) — the deferred balance trigger that validates the atomic-event semantics.
- ADR-0019 (Bulk import primitives in core; import management in brokerage) — broker-statement imports use this rule to shape the Transactions they produce.
- ADR-0027 (Broker import schemas — code-only registry) — schemas are the import path's callers of these templates.
- OQ-7 in `open-questions.md` is resolved by this ADR.
