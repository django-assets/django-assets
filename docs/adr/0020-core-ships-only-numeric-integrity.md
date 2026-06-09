# ADR-0020: Core ships only numeric integrity

## Status

Accepted — 2026-06-03

## Context

Throughout the design process, the boundary between `django_assets.core` and its sibling sub-packages has been gradually clarified. ADR-0011 framed core as "the ledger primitive, not a corporate-actions tracker." Later ADRs (option contract model, account capability flags, currency/crypto metadata extensions, instrument categorization) added opinionated fields and models to core that drift from that framing.

A worked example from design review made the architectural question concrete. A customer holds a long-term AAPL position with cost basis from years ago. The customer buys a small additional position before earnings as a swing trade. After earnings, the customer sells the swing-trade quantity at a loss because the trade didn't work. The customer tags both legs of that activity as a single "trade" in their mental model.

Two equally true P&L stories exist for this activity:

- **Trade-grouping view** (the customer's intent): the swing trade lost money. Entry cost greater than exit proceeds.
- **Tax-FIFO view** (the IRS's default): the sale matched the oldest lots, which have very low cost basis. The realized gain is a substantial long-term capital gain.

Neither view is wrong. Both describe the same underlying ledger movements under different non-fungible accounting rules. The customer cares about trade P&L; their accountant cares about cost-basis P&L; both reports are correct.

This reveals a more general truth: **non-fungible accounting is not a single layer — it's a family of layers, each with its own grouping and matching rules.** Tax lots (FIFO/LIFO/HIFO/Specific/Average Cost), user-defined trade groupings, sector attribution, strategy attribution, risk-bucketed P&L, factor-based performance — each is a valid view, and they coexist over the same underlying ledger.

If core ships any one non-fungible accounting model, it implicitly privileges that model over the others. Hosts and adopters either accept the prescribed model or work around it. Either way, core has taken an opinionated position.

The cleaner architectural position is: **core ships only numeric integrity.** No categorization vocabulary. No semantic enums. No tax-aware or trade-aware structures. No matching strategies. No allocations. Just fungible unit movement and the math constraints that ensure those movements balance.

Every opinionated structure lives in a sibling sub-package. A host that wants a particular view installs the corresponding app. A host that wants multiple views installs multiple apps. A host that needs none of them uses core alone and is a perfectly valid adopter (e.g., a crypto treasury system that just tracks balances).

This is the load-bearing principle for the entire `django-assets` distribution.

## Decision

### The principle

> Core ships only numeric integrity. It is unopinionated. It holds only numeric truth.

### The Inviolability Rule

> **Sub-apps can categorize, decompose, or re-interpret transactions on their own ledgers. They cannot modify the actual cash numbers touching accounts in the core ledger.**

This is the rule that keeps core inviolable. Every precision view (trades, lots, host-built attribution, anything) builds its own analytical model on its own books, reading from core's facts. Core's amounts and balances are the canonical source; sub-package interpretations are layered on top, not embedded inside.

A sub-package may decide that a given cash leg "means" different things (basis recovery + realized profit, or trade revenue + commission rebate, or whatever its analytical model requires). That interpretation lives in the sub-package's own tables. The core leg's amount, account, and instrument are unchanged. Two sub-packages may decompose the same leg differently — and that's fine, because they're on different books.

### What core enforces

Concretely:

- **Core enforces fungible-unit math.** Per-instrument zero-sum across the legs of each Transaction (the deferred balance trigger from ADR-0004). Per-instrument precision rules. Identifier lookup integrity. That is the entire integrity surface.
- **Core does not encode accounting semantics.** No tax-lot fields. No allocations. No realized-vs-unrealized distinction. No principal-vs-gain categorization. No buy/sell/dividend type discriminator.
- **Core does not categorize instruments.** No `kind` enum (equity/option/currency/crypto/etc.). No per-asset-type metadata extension models. No derivative relationship FKs (`underlying`). No corporate-action relationship FKs (`successor`).
- **Core does not categorize accounts.** No subtype labels. No capability flags. No system-account discriminator. Every Account is just (owner, name).
- **Core does not provide non-fungible primitives.** No Lot, no LotMatch, no LegAllocation, no tags.

### What stays in core

The complete core model set:

```python
class Exchange(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=40)

class Instrument(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=64, db_index=True)
    quantity_decimals = models.PositiveSmallIntegerField(default=4)
    price_decimals = models.PositiveSmallIntegerField(default=4)
    multiplier = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    price_currency = models.ForeignKey("self", related_name="+", null=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

class Identifier(models.Model):
    instrument = models.ForeignKey(Instrument, related_name="identifiers", on_delete=models.CASCADE)
    type = models.CharField(max_length=20)
    value = models.CharField(max_length=64)
    exchange = models.ForeignKey(Exchange, null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["type", "value", "exchange"],
                condition=models.Q(is_active=True),
                name="uniq_active_identifier",
            ),
        ]

class Account(models.Model):
    id = models.BigAutoField(primary_key=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="accounts", db_index=True,
    )
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

class Transaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    account = models.ForeignKey(Account, related_name="transactions", on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    trade_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

class TransactionLeg(models.Model):
    transaction = models.ForeignKey(Transaction, related_name="legs", on_delete=models.CASCADE)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=40, decimal_places=18)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
```

Core's integrity machinery:

- The deferred balance trigger (ADR-0004) — per-instrument zero-sum per Transaction.
- The partial unique constraint on `Identifier` for active rows (ADR-0009).
- Precision domains (`dec18` and friends) for scale-checked decimals.

Core's query APIs:

- `Portfolio.at(account, as_of)` — fungible holdings, computed by aggregation (ADR-0016).
- `Holding.current(account, instrument)` — fungible balance.
- `Instrument.resolve(value, ...)` and `Instrument.search(value, ...)` — identifier lookup (ADR-0018).

Core's bulk-insertion primitive:

- `TransactionBuilder.bulk_import(rows)` — efficient batched insertion (ADR-0019).
- `TransactionBuilder.delete_range(account, from, to, confirm)` — efficient range deletion (ADR-0019).

Nothing else.

### What moves to sibling sub-packages

| Moved structure | Lands in |
| --- | --- |
| `Instrument.kind`, `Instrument.primary_exchange`, `Instrument.underlying`, `Instrument.successor` | `django_assets.brokerage` provides asset-type-specific extension models that carry the relationships sub-packages need (e.g., an `OptionMeta` with an `underlying` FK to Instrument). |
| `OptionMeta`, `Deliverable`, `CorporateAction` | `django_assets.brokerage` |
| `CurrencyMeta`, `CryptoMeta`, future `EquityMeta` / `BondMeta` / `FutureMeta` | `django_assets.brokerage` (consolidated; can be split out to a dedicated app if scope demands) |
| `Account.kind` (user/external/issuer/treasury/counterparty discriminator) | Removed; replaced by naming convention. The "external" or "counterparty" role is just a regular Account with a descriptive name. |
| `Account.instrument` (issuer account FK) | Removed; issuer-style accounts are an opt-in pattern that hosts implement via convention if needed. |
| `Account.subtype`, `allows_short`, `allows_margin`, `is_tax_advantaged`, `tax_treatment` | `django_assets.brokerage` |
| User-defined trade groupings and `TradeAllocation` (trade-view leg decomposition) | `django_assets.trades` |
| `Lot`, `LotMatch`, matching strategies, wash sale, 1099-B reporting | `django_assets.lots` |
| Any intra-leg decomposition / cash-flow categorization model (LegAllocation-style) | Each precision view in its own sibling sub-package. Core does NOT ship a generic LegAllocation primitive. Sub-apps each define their own decomposition model that satisfies their own integrity rules on their own books. |

### Multiple coexisting precision views

A real deployment can install several precision apps simultaneously. Each reads the same fungible ledger and computes its own answers under its own rules. The customer's earnings-swing example produces a **loss** in the trades view and a **long-term capital gain** in the tax-lots view simultaneously, and both are surfaced to the user.

The principle that makes this work: **the underlying ledger is the same; precision views differ in their rules and outputs.** Core enforces the ledger's correctness. Each precision app owns its own rules and outputs.

### Sub-apps build their own ledgers — worked example

Consider a short-option roundtrip:

1. User sells 2 HIMS Dec 18 2026 $30 calls short. Net premium received: $1,569.04 (principal $1,570.00 minus $0.90 commission minus $0.06 industry fee).
2. Later, with the trade now profitable, user buys back the same 2 contracts for $1,000.00. Net profit on the trade: $569.04.

**Core's ledger** (the inviolable facts — actual cash movements):

Transaction T1 (sell):
```
-2 HIMS_CALL    from user_brokerage
+2 HIMS_CALL    to external
+$1,569.04 USD  to user_cash
+$0.90    USD  to user_commissions_paid
+$0.06    USD  to user_fees_paid
-$1,570.00 USD  from external_counterparty
```

Transaction T2 (buy back):
```
+2 HIMS_CALL    to user_brokerage
-2 HIMS_CALL    from external
-$1,000.00 USD  from user_cash
+$1,000.00 USD  to external_counterparty
```

Net change to `user_cash` across both transactions: `+$1,569.04 − $1,000.00 = +$569.04`. The deferred balance trigger validates per-instrument zero-sum on each transaction independently.

**Trades' ledger** (interpretation on its own books — reads from core but writes to its own tables):

The user tags T1 and T2 as one logical "trade." `django_assets.trades` records this grouping and, on its own analytical books, decomposes the cash legs into trade-view categories:

```python
trade = Trade(user=..., name="HIMS earnings swing", state="closed")

# Trades' own decomposition table — NOT in core
TradeAllocation(trade=trade, source_leg=<T1 user_cash leg>, category="revenue", amount=+1569.04)
TradeAllocation(trade=trade, source_leg=<T2 user_cash leg>, category="cost",    amount=-1000.00)
TradeAllocation(trade=trade, source_leg=<T2 user_cash leg>, category="basis_recovery", amount=-1000.00)
TradeAllocation(trade=trade, source_leg=<T1 user_cash leg>, category="realized_profit", amount=+569.04)
```

Trade P&L derived: revenue $1,569.04 − cost $1,000.00 = profit $569.04. The trades sub-package surfaces "HIMS earnings trade: +$569.04" to the user. **The core legs are unchanged.**

**Lots' ledger** (cost-basis matching — also on its own books):

`django_assets.lots` reads the same core transactions and produces its own decomposition under whatever matching strategy is configured (FIFO/LIFO/HIFO/Specific). For long-stock trades the lots view typically shows "basis recovery + realized gain/loss" splits; for short-option roundtrips the lots view treats the open premium as proceeds at open and the close cost as basis at close. Different sub-packages can disagree about the analytical decomposition; both are valid in their own books.

### Where intra-leg decomposition models live

Each precision view that needs to decompose a leg's amount has its OWN model in its OWN app. None of these live in core:

- `django_assets.trades.TradeAllocation` — splits leg amounts into trade-view categories (revenue/cost/profit/loss/basis_recovery).
- `django_assets.lots.LotMatch` — matches sale legs against acquisition lots, decomposing proceeds into basis-recovery and realized-gain.
- Host-built precision views — each ships its own split table if it needs one.

Different sub-packages can disagree about how a single leg "splits" — and that's fine, because the splits live in different books. The core leg's amount, account, and instrument are inviolable.

Capturing **source-of-truth fee breakdowns** (commission, regulatory fee, exchange fee, etc.) is achieved through **multi-leg routing to user-owned tracking accounts** in core. The example above shows this: `user_commissions_paid` and `user_fees_paid` are first-class core Accounts. Their balances directly answer "how much have I paid in commissions YTD?" via `Holding.current(user_commissions_paid, USD)`. The convention for setting up tracking accounts is documented in `django_assets.brokerage`; the accounts themselves are plain core Account rows.

### Distribution shape

The single PyPI distribution (per ADR-0015) ships one Django app (`django_assets`) organized into four Python sub-packages:

```
django-assets/   (PyPI distribution)
└── django_assets/   (Django app, app_label = "django_assets")
    ├── core/        # numeric integrity
    ├── brokerage/   # transaction templates, import mgmt, instrument extensions
    ├── trades/      # user-defined trade groupings and trade-level P&L
    └── lots/        # tax-lot tracking, matching strategies, 1099-B reporting
```

A host installs the single app `django_assets` in `INSTALLED_APPS`. The sub-package separation is enforced by code organization and convention, not by Django's app boundary — the Inviolability Rule holds at the import level (core modules don't depend on brokerage/trades/lots; opinionated modules build their own analytical views on top of core's facts).

## Consequences

**Easier:**

- Core's purpose is sharp. Its responsibility is one thing: balanced fungible movement. That responsibility never grows.
- Adopters install one Django app and get the full functionality; sub-package organization is purely a code-organization concern for the package maintainers.
- Adopters who need multiple precision views (trade-based P&L for the user, tax-based P&L for the accountant) get all of them simultaneously.
- New precision views (sector attribution, factor performance, strategy P&L, regulatory categorization) can be added as additional sibling sub-packages within the same Django app, without changing core's models or app boundary.
- The distinction between "the ledger is correct" and "this report is meaningful" stays clean. The first is core's job; the second is a precision app's job.
- Future contributors do not have to guess where things go. The principle answers it: if it adds opinion, it goes in a sibling sub-package.

**Harder:**

- Existing ADRs that put opinionated structures in core have to be revised. Several do.
- The Inviolability Rule is enforced by code organization and code review rather than by Django's app boundary. For the single-developer phase this is fine; if the project later grows multiple contributors, code review discipline becomes the load-bearing enforcement mechanism.
- The `django_assets.brokerage` sub-package absorbs a lot: transaction templates, import management, instrument metadata extensions, account capability flags. This is justifiable because all of those serve the "common retail brokerage workflow" use case, but the sub-package surface is large.

**Deferred:**

- Whether `django_assets.brokerage`'s scope justifies splitting out an instrument-extensions sub-package later (`django_assets_extensions`?). Not in v0.1. If brokerage's surface becomes unwieldy, a future ADR can split it.
- Whether trades and lots should share any infrastructure (e.g., a common "match" abstraction). Not in v0.1. Each defines its own primitives.

## Related

- ADR-0011 (Core is the ledger) — this ADR strengthens it. Where ADR-0011 said "core is the ledger primitive, not a corporate-actions tracker," this ADR sharpens it to "core ships only numeric integrity, full stop."
- ADR-0015 (single PyPI distribution, single Django app, organized into sub-packages) — the packaging decision that this ADR's principle is enforced within.
- ADR-0009, ADR-0010, ADR-0013, ADR-0014, ADR-0017 — revised in light of this ADR to reflect the narrowed core.
- ADR-0019 (bulk import) — unchanged; `bulk_import` is a structural primitive for efficient insertion, not opinionated.
- Resolves OQ-14 (Realized P&L scope) and OQ-17 (Lot tracking schema readiness) by reference: realized P&L is computed by `django_assets.lots` (cost-basis matching) and `django_assets.trades` (user-defined grouping); lot tracking is in `django_assets.lots`.
