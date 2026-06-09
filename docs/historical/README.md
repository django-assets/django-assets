# Historical Design Documents

This directory contains design and requirements documents written during the early planning phase of `django-assets`, **before** the project adopted Architecture Decision Records (ADRs) as its source of truth.

## Status

These documents are **not authoritative**. They are preserved here as historical context — useful for understanding how the design evolved, the constraints that drove it, and the use cases the project was originally scoped against.

For current, authoritative design decisions, see [`docs/adr/`](../adr/).

When the contents of these documents conflict with an accepted ADR, **the ADR is correct**.

## What's here

| File | Original purpose | Current status relative to ADRs |
| --- | --- | --- |
| `host_compatibility_requirements.md` | Captured the primary target host's environment constraints (Django, PG, pytest config, etc.). Drove the initial set of ADRs. | Constraints are still real but the document predates the final package shape. Use ADRs for current decisions; this document remains useful for *why* certain constraints exist. |
| `django_assets_brokerage_requirements.md` | Specified the transaction-template surface for the brokerage sub-package. | Template surface still valid as an inventory of what brokerage covers. Several models (`AccountProfile`, `ImportBatch`, `OptionMeta`, etc.) and the tracking-account fee convention from ADRs 0014/0019/0020/0021 are not yet integrated. Internal framing ("optional Django app") predates ADR-0015's single-app + sub-package model. |
| `django_assets_trades_requirements.md` | Specified the trade-grouping and tagging system. | Core trade-grouping concepts still valid. Missing the `TradeAllocation` decomposition introduced by ADR-0020's Inviolability Rule. Overlaps with the newer `lots` sub-package (per ADR-0020) on P&L scope; the split (trades = user-defined view; lots = tax-FIFO/LIFO/HIFO view) is documented in the ADRs, not here. |
| `django_assets_core_extension_patterns_guide.md` | Overview of extension patterns for hosts to build on top of core. | Conceptual approach still useful. Pattern 6 (tax lot tracking) is largely subsumed by the `lots` sub-package as established in ADR-0020. |
| `django_assets_core_extension_pattern_1_foreign_keys.md` | FK-based extension pattern. | Still useful as a host-extension recipe. Import paths predate the single-app sub-package structure (ADR-0015). |
| `django_assets_core_extension_pattern_2_metadata.md` | Metadata-based extension pattern. | Still useful. Includes an example of FIFO/LIFO as a metadata extension that is now better handled by the `lots` sub-package. |
| `django_assets_core_extension_pattern_3_transaction_templates.md` | Building custom transaction templates. | Still useful. Should be read alongside ADR-0021 (brokerage templates follow the source's transaction shape). |
| `django_assets_core_extension_pattern_4_querying_reporting.md` | Querying and reporting patterns. | Still useful. Import paths predate ADR-0015. |
| `django_assets_core_extension_pattern_6_tax_lot_tracking.md` | Building tax-lot tracking as a host extension. | Largely obsolete — the `lots` sub-package (per ADR-0020) provides tax-lot tracking as first-class functionality. |
| `django_assets_core_price_connectors_guide.md` | Price connector interface specification. | Conceptual content (`PriceConnector` ABC, caching, rate-limiting, batch) still valid. Import paths and connector class names have been generalized. |

## When these documents will be retired

These documents will be retained until the relevant features are implemented and documented in the actual package codebase. At that point each document becomes redundant with code-level documentation and the ADRs, and can be removed.

If you are reading one of these documents and find a conflict with the ADRs, **trust the ADR** and consider filing an issue to update or remove the historical document.
