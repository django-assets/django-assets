# Extension Patterns for django_assets_core

### *How developers extend and build on top of `django_assets_core`.*

## Overview

The `django_assets_core` app exposes all core models (`Account`, `Instrument`, `Transaction`, `TransactionLeg`) as standard Django models. This allows developers to:

1. **Reference with ForeignKeys** from their own models
2. **Query with Django ORM** for reporting and analysis
3. **Extend with relationships** (one-to-one, foreign keys, generic relations)

**This document provides an overview of extension patterns for building your own custom functionality** on top of the core app. Each pattern is documented in detail in separate documents linked below.

**Optional sibling apps** (shipped in the same `django-assets` PyPI distribution; see ADR-0015):
- **`django_assets_trades`** — Trade management and tagging system. See `django_assets_trades_requirements.md` for details.
- **`django_assets_brokerage`** — High-level transaction templates for brokerage accounts. See `django_assets_brokerage_requirements.md` for details.

This document focuses on **user-created extensions** — patterns you can use to build your own custom functionality.

## Table of Contents

1. [Core Concepts](#core-concepts)
2. [Model Exposure](#model-exposure)
3. [Extension Patterns Overview](#extension-patterns-overview)
4. [Combining Patterns](#combining-patterns)
5. [Best Practices](#best-practices)
6. [Related Documents](#related-documents)

## Core Concepts

### Django-Native Models

All core models are standard Django models that you can:

- Reference with `ForeignKey` relationships from your own models
- Query with standard Django ORM (`Transaction.objects.filter(...)`)
- Extend with relationships (one-to-one, foreign keys, generic relations)

### Extension Philosophy

The core package provides building blocks. You build on top of them:

- **Core models**: `Account`, `Instrument`, `Transaction`, `TransactionLeg` - use these as building blocks
- **Core APIs**: `TransactionBuilder`, `Portfolio.at()` - use these to create transactions and query holdings
- **Your models**: Create your own models that reference core models
- **Your APIs**: Build custom transaction templates, reporting functions, etc.

## Model Exposure

All core tables are exposed as Django models that developers can:

* Reference with `ForeignKey` relationships
* Query with standard Django ORM (`Transaction.objects.filter(...)`)
* Extend with related models in their own applications

### Core Models Available

* `django_assets_core.models.Account` — Brokerage/bank/wallet accounts
* `django_assets_core.models.Instrument` — Tradable/currency assets
* `django_assets_core.models.Transaction` — Transaction header (source of truth)
* `django_assets_core.models.TransactionLeg` — Double-entry ledger entries
* `django_assets_core.models.Holding` — Derived holdings (materialized view)
* `django_assets_core.models.Exchange` — Exchange metadata

### Model Relationships

* `Transaction.account` — ForeignKey to Account
* `Transaction.transaction_legs` — Reverse relationship to TransactionLeg objects
* `TransactionLeg.transaction` — ForeignKey to Transaction
* `TransactionLeg.account` — ForeignKey to Account
* `TransactionLeg.instrument` — ForeignKey to Instrument
* `Transaction.metadata` — JSONField for flexible developer extensions
* `Transaction.description` — TextField for human-readable notes
* `TransactionLeg.metadata` — JSONField for flexible per-leg extensions
* `TransactionLeg.description` — TextField for per-leg human-readable notes

## Extension Patterns Overview

The following patterns show common ways to extend `django_assets_core` with your own functionality. Each pattern is documented in detail in its own document:

### Pattern 1: Foreign Key Relationships

**Document**: `django_assets_core_extension_pattern_1_foreign_keys.md`

The most common pattern: create models in your own application that reference core models with foreign keys. This allows you to build domain-specific models that link to transactions, accounts, and instruments.

**Use cases:**
- Custom trade models that group related transactions
- Portfolio models that aggregate accounts
- Strategy models that track trading strategies
- Any domain-specific model that needs to reference core assets

**Key concepts:**
- Use `ForeignKey` to reference `Transaction`, `Account`, or `Instrument`
- Use `on_delete=models.PROTECT` to prevent accidental deletion
- Leverage reverse relationships for efficient queries

### Pattern 2: Metadata on Transactions and Legs

**Document**: `django_assets_core_extension_pattern_2_metadata.md`

Add structured or unstructured metadata to transactions and transaction legs. Two approaches: JSON metadata fields (flexible) or one-to-one relationships (queryable).

**Use cases:**
- Store trade IDs, strategy names, broker references
- Track settlement dates, exchange information
- Add custom fields without schema changes (JSON)
- Build queryable metadata for reporting (one-to-one)

**Key concepts:**
- `Transaction.metadata` and `TransactionLeg.metadata` JSONFields for flexible data
- One-to-one models for structured, queryable metadata
- Per-leg metadata for settlement dates, exchanges, broker references

### Pattern 3: Custom Transaction Templates

**Document**: `django_assets_core_extension_pattern_3_transaction_templates.md`

Build high-level APIs using `TransactionBuilder` for domain-specific operations. Create reusable functions that encapsulate common transaction patterns.

**Use cases:**
- Custom buy/sell functions with specific fee handling
- Dividend recording with tax withholding
- Complex multi-leg transactions
- Domain-specific transaction types

**Key concepts:**
- Use `TransactionBuilder` to create balanced double-entry transactions
- Encapsulate transaction logic in reusable functions
- Handle fees, taxes, and complex scenarios

### Pattern 4: Querying and Reporting

**Document**: `django_assets_core_extension_pattern_4_querying_reporting.md`

Use standard Django ORM to build powerful queries and reports. Combine core models with your extension models for comprehensive analysis.

**Use cases:**
- Trade P/L reports
- Transaction analysis
- Portfolio summaries
- Performance metrics

**Key concepts:**
- Use Django ORM with `select_related()` and `prefetch_related()`
- Combine core models with extension models
- Leverage `Portfolio.at()` for holdings queries
- Build custom reporting functions

### Pattern 5: Price Connectors

**Document**: `django_assets_core_price_connectors_guide.md`

Build connectors that retrieve prices from external APIs for portfolio valuation. The core system stores only transaction records; prices are retrieved on-demand.

**Use cases:**
- External market-data API integration
- Broker API price feeds
- Crypto exchange price feeds
- Custom price sources

**Key concepts:**
- Base connector interface
- Reference HTTP connector
- Custom connector implementation
- Caching and rate limiting

### Pattern 6: Tax Lot Tracking

**Document**: `django_assets_core_extension_pattern_6_tax_lot_tracking.md`

Implement cost basis accounting using FIFO/LIFO/AVG cost methods. Derive prices from transaction legs rather than storing them separately.

**Use cases:**
- Cost basis tracking
- Tax lot management
- Realized gain/loss calculation
- FIFO/LIFO/AVG cost methods

**Key concepts:**
- Derive price from transaction legs: `price = |cash_leg| / |asset_leg|`
- Never store price per unit separately
- Create tax lot models linked to transaction legs
- Implement lot disposition logic

## Combining Patterns

Real-world applications often combine multiple patterns:

```python
# Pattern 1: Custom transaction template
def create_trade(opening_trans, closing_trans):
    transaction = custom_buy_with_fees(...)
    
    # Pattern 2: Create trade model (foreign key)
    trade = Trade.objects.create(
        opening_transaction=transaction,
        ...
    )
    
    # Pattern 3: Add metadata (one-to-one)
    TransactionMetadata.objects.create(
        transaction=transaction,
        trade_id=trade.trade_id,
        strategy="momentum"
    )
    
    return trade
```

See individual pattern documents for detailed examples of combining patterns.

## Best Practices

1. **Use `on_delete=models.PROTECT`** for foreign keys to core transactions to prevent accidental deletion
2. **Use `select_related()` and `prefetch_related()`** for efficient queries
3. **Index frequently queried fields** in your extension models
4. **Use JSON metadata for flexible data**, structured models for queryable data
5. **Always validate** that your custom templates create balanced transactions
6. **Use Django migrations** for schema changes in extension models
7. **Use transaction leg descriptions** to clearly identify which leg represents asset vs. cash vs. fees
8. **Store settlement dates and exchange info on legs** using `TransactionLeg.metadata` for per-leg information
9. **Derive prices from transaction legs** rather than storing them separately - price = |cash_leg| / |asset_leg|
10. **Build price connectors for valuations**: Use connectors to retrieve prices from external APIs. Never store prices in the core system - only transaction records are stored.

## Related Documents

### Extension Pattern Details

* **`django_assets_core_extension_pattern_1_foreign_keys.md`** — Foreign key relationships pattern
* **`django_assets_core_extension_pattern_2_metadata.md`** — Metadata on transactions and legs
* **`django_assets_core_extension_pattern_3_transaction_templates.md`** — Custom transaction templates
* **`django_assets_core_extension_pattern_4_querying_reporting.md`** — Querying and reporting
* **`django_assets_core_extension_pattern_6_tax_lot_tracking.md`** — Tax lot tracking

### Other Documentation

* **`README.md`** — Core package scope and features
* **`django_assets_trades_requirements.md`** — Requirements for the `django_assets_trades` app (trade management and tagging)
* **`django_assets_brokerage_requirements.md`** — Requirements for the `django_assets_brokerage` app (transaction templates)
* **`django_assets_core_price_connectors_guide.md`** — Comprehensive guide to building custom price connectors
