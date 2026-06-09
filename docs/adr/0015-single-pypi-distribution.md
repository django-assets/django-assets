# ADR-0015: Single PyPI distribution, single Django app, organized into sub-packages

## Status

Accepted — 2026-06-03

## Context

Early planning treated `django-assets-core`, `django-assets-brokerage`, and `django-assets-trades` as three separate PyPI packages. A subsequent revision collapsed them into one PyPI distribution shipping multiple Django apps. A further revision now collapses them into **one PyPI distribution shipping one Django app** organized internally into Python sub-packages.

The factor that drove this final simplification: the package has exactly one sure-fire adopter for the foreseeable future, and that adopter will install every part of the package. Splitting the codebase into multiple Django apps imposed costs (four migrations folders, four admin registrations, four `apps.py` files, cross-app `ForeignKey` string references, migration ordering concerns) without delivering the corresponding benefit (selective installation by adopters who only want part of the functionality). That benefit serves a hypothetical audience that does not exist today.

The "core ships only numeric integrity" principle from ADR-0020 does not require separate Django apps to hold. It can be enforced at the Python module level — `django_assets.core.models` contains only the numeric-integrity models; `django_assets.brokerage.models` contains the opinionated extensions; and so on. The Inviolability Rule (sub-packages may decompose or re-interpret transactions on their own books but cannot modify core's amounts) is a code-organization discipline that does not require Django's app boundary to be enforced.

If a future adopter needs selective installation — a "pure ledger" use case (treasury management, crypto wallet, custom non-broker system) — the single app can be split into multiple Django apps at that point. Splitting an existing Django app is a known refactoring pattern (move files, rewrite migrations with new `app_label`, update FK string references). The reverse direction (merging apps) has the same effort. Starting simple and splitting later is the lower-cost path.

## Decision

### One PyPI distribution, one Django app

The PyPI distribution name is `django-assets`. It ships a single Django app named `django_assets` whose `app_label` is `"django_assets"`.

```
django-assets/                  # PyPI distribution
├── pyproject.toml
├── django_assets/              # Django app (app_label = "django_assets")
│   ├── apps.py
│   ├── migrations/             # one migrations folder, covering all models
│   ├── admin.py                # all admin registrations
│   ├── core/                   # Python sub-package — numeric integrity
│   │   ├── models.py           # Account, Instrument, Identifier, Exchange,
│   │   │                       # Transaction, TransactionLeg
│   │   ├── triggers.py         # balance trigger + DDL
│   │   ├── resolvers.py        # InstrumentResolver
│   │   ├── builder.py          # TransactionBuilder
│   │   └── ...
│   ├── brokerage/              # Python sub-package — opinionated extensions
│   │   ├── models.py           # AccountProfile, OptionMeta, Deliverable,
│   │   │                       # CorporateAction, CurrencyMeta, CryptoMeta,
│   │   │                       # ImportBatch, TransactionImport, ...
│   │   ├── templates.py        # buy_shares, sell_option, exercise_option, ...
│   │   ├── importers.py        # ImportBatch helpers, dedup helpers
│   │   └── ...
│   ├── trades/                 # Python sub-package — user-defined trade groupings
│   │   ├── models.py           # Trade, TradeAllocation, Tag, TagCategory
│   │   └── ...
│   └── lots/                   # Python sub-package — tax-lot tracking
│       ├── models.py           # Lot, LotMatch
│       ├── strategies.py       # FIFO, LIFO, HIFO, Specific, Average Cost
│       └── ...
└── dev_project/                # standalone development project
    └── ...
```

Adopters install once:

```bash
pip install django-assets
```

And enable the single app:

```python
INSTALLED_APPS = [
    # ...
    "django_assets",
]
```

That's it. All models migrate; all admin registers; all helpers are importable.

### Module responsibilities (the conceptual separation, enforced by code review)

ADR-0020's principle is preserved at the **module level** rather than the Django app level:

- **`django_assets.core`** — numeric integrity only. Account, Instrument, Identifier, Exchange, Transaction, TransactionLeg. Balance trigger. Precision domains. Live aggregation (Portfolio.at, Holding.current). InstrumentResolver. TransactionBuilder.bulk_import.
- **`django_assets.brokerage`** — opinionated extensions and templates. AccountProfile (capability flags), per-asset-type metadata extensions (OptionMeta, Deliverable, CurrencyMeta, CryptoMeta, ...), transaction templates (buy_shares, sell_option, exercise_option, ...), ImportBatch and dedup helpers, recommended account-naming conventions.
- **`django_assets.trades`** — user-defined trade groupings. Trade, TradeAllocation, hierarchical parent/child, tagging.
- **`django_assets.lots`** — tax-lot tracking. Lot, LotMatch, matching strategies (FIFO/LIFO/HIFO/Specific/Average Cost), wash sale adjustments, 1099-B-style reports.

Code review enforces the discipline that no `core` module imports from `brokerage`, `trades`, or `lots`; that `brokerage` may import from `core` only via public APIs; that `trades` and `lots` build their own analytical views on top per the Inviolability Rule.

### FK references use the single app_label

Because all models share `app_label = "django_assets"`, all Django string FK references use that label:

```python
# In trades/models.py
class TradeAllocation(models.Model):
    source_leg = models.ForeignKey("django_assets.TransactionLeg", on_delete=models.CASCADE)
```

Python imports use the sub-package paths:

```python
from django_assets.core.models import Transaction, TransactionLeg
from django_assets.brokerage.models import OptionMeta
from django_assets.trades.models import Trade, TradeAllocation
from django_assets.lots.models import Lot, LotMatch
```

### Versioning

The package has one version number. Releasing `django-assets==0.2.0` releases everything together. Refactors across sub-packages are one PR, one release. There is no cross-package compatibility matrix to maintain.

### Integration/data add-ons live in separate PyPI packages

Packages that ingest external data or integrate with third-party systems are NOT in the main distribution:

- `django-assets-occ-feed` — OCC memo ingestion
- `django-assets-broker-import` — broker statement parsers (Schwab, Fidelity, IB, etc.)
- `django-assets-us-corp-actions` — US equity corporate-action ingestion
- `django-assets-fx-rates` — FX rate registry / provider adapter

These have different release cadences, different dependency footprints (HTTP libraries, provider SDKs, CSV/QFX parsers), and different audiences. They install separately and depend on `django-assets` as a pinned version range.

### Future split is reversible

If a real reason to split the single Django app into multiple apps emerges later — a "pure ledger" adopter requests selective installation, or sub-package release cadences diverge — splitting is a known refactoring: assign different `app_label`s to model subsets, generate migrations that retag the underlying tables, update FK string references. The single-app starting point does not foreclose this; it just defers the cost until the benefit is real.

## Consequences

**Easier:**

- One PyPI install. One INSTALLED_APPS entry. One migrations folder. One admin registration. One CI pipeline. One issue tracker.
- No cross-app FK string references to track or migrate. No app loading order concerns.
- Refactoring across sub-package boundaries is fast and uncoordinated.
- Adopters cannot install the package wrong by forgetting to enable a required app.
- Development feels like working on one cohesive codebase rather than four loosely-related ones.

**Harder:**

- The discipline that keeps ADR-0020's principle intact (core's modules don't import opinionated structures; opinionated modules don't reach into core internals) is now enforced by code review and convention, not by Django's app boundary. For a single-developer project this is fine; if the project ever grows multiple contributors with different opinions, the boundary may need to be re-established.
- Adopters who want partial install (a hypothetical "pure ledger" use case) have to wait for a future split. Today there are no such adopters.
- The PyPI distribution's import surface is somewhat larger than it would be in a split design (all sub-packages' Python is loaded when `django_assets` is on the path), but the cost is trivial — kilobytes of Python source.

**Deferred:**

- Splitting the single Django app into multiple apps if and when selective installation becomes a real demand. Not in v0.x.

## Related

- ADR-0020 (Core ships only numeric integrity) — the principle that justifies keeping `core`'s module separate even within one Django app.
- ADR-0011, ADR-0017, ADR-0019, ADR-0021 — all reference the sub-package organization established here.
- OQ-16 (cross-package version pinning) was previously resolved by the multi-app design; with a single app, cross-package pinning is trivially N/A.
