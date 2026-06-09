# django-assets

Precise storage & arithmetic for currencies, crypto, equities, options, futures, bonds, and indices in Django / PostgreSQL.

`django-assets` is a single PyPI distribution that ships three Django apps:

- **`django_assets_core`** — the ledger primitive (models, integrity, query APIs). Required.
- **`django_assets_brokerage`** — high-level transaction templates (`buy_shares`, `dividend_paid`, `exercise_option`, etc.). Optional.
- **`django_assets_trades`** — trade grouping, tagging, and realized P&L. Optional.

Install once; add only the apps you want to `INSTALLED_APPS`. See ADR-0015 in `adr/` for the rationale.

## Project Scope

### *Precise, auditable, and Postgres-native financial asset storage for Django.* Core double-entry ledger engine with exact math, database-enforced integrity, and time-travel portfolio queries.

## Mission Statement

`django-assets` provides **exact, verifiable accounting and position tracking** for any financial instrument using **PostgreSQL's strongest numeric and constraint features**.

It gives Django developers a **ledger-grade foundation**: exact math (`NUMERIC`), database-enforced integrity (balanced transaction legs, scale checks), and a clean, human-readable schema.

## Core Principles

* **Transaction-first architecture**: transactions are the source of truth. Users add transactions first, and holdings are automatically derived from transaction history.
* **Time-travel portfolios**: portfolios can be viewed at any specific date/time by aggregating transactions up to that point in time.
* **Postgres-first**: rely on PostgreSQL capabilities unapologetically — `NUMERIC`, domains, triggers, partitions, deferred constraints.
* **Exactness > performance**: never use floats; everything is `Decimal` in Python and `NUMERIC` in SQL.
* **Database as source of truth**: correctness is enforced in the database, not just in Django models.
* **Double-entry ledger storage**: all transactions are stored in double-entry format. The ledger enforces balanced transaction legs per instrument (shares, BTC, USD, contracts) in their native units. This is mandatory - double-entry provides auditability and integrity guarantees.
* **Units-first design**: all transaction legs (both currency and asset sides) use units at all times — currency amounts, share quantities, option contracts, crypto units, etc.
* **Low-level primitives**: the `django_assets_core` app provides building blocks (`TransactionBuilder`, direct transaction leg creation) that enforce double-entry integrity. High-level transaction templates are provided by the optional `django_assets_brokerage` app (shipped in the same distribution).
* **Composable & extensible**: new asset types (e.g., warrants, CFDs) plug in via metadata tables.
* **Human-readable schema**: clear text codes, small meta tables, minimal JSON.
* **Django-native models**: all core models (`Account`, `Instrument`, `Transaction`, `TransactionLeg`) are standard Django models that developers can reference with foreign keys, query with ORM, and extend in their own applications.
* **Django-native ergonomics**: clean models, admin integration, DRF serializers, standard Django ORM queries.

## Goals

### Core Capabilities

* **Transaction-driven holdings**: holdings are automatically calculated from transaction history. Users add transactions first, and the system derives current and historical positions.
* **Time-based portfolio queries**: query portfolio composition at any specific date/time by aggregating transactions up to that point.
* Precise storage using unconstrained PostgreSQL `NUMERIC`.
* **Per-instrument precision rules** (`quantity_decimals`, `price_decimals`, `multiplier`).
* Enforced **balanced transaction legs** at the DB level via a deferred trigger.
* Built-in **measure** abstraction (`Measure(amount, unit)`).
* **Django models**: `Account`, `Instrument`, `Transaction`, `TransactionLeg`, `Holding` are standard Django models that developers can reference with foreign keys, extend with relationships, and query with ORM.
* **Double-entry storage**: transactions are stored internally as balanced journal entries (transaction legs) in double-entry format, ensuring accounting integrity.
* **Low-level transaction APIs**: `TransactionBuilder` and direct transaction leg creation for building balanced double-entry transactions.
* **Metadata support**: `Transaction` model includes `metadata` JSON field and `description` field for flexible developer extension.
* **Transaction leg metadata**: `TransactionLeg` model includes `metadata` JSON field and `description` field for per-leg extensions (e.g., settlement dates, exchange information).
* Reference data model for exchanges, identifiers, and asset-specific metadata (currency, equity, option, future, bond, crypto).
* **Extensible design**: developers can build their own transaction templates, create extension models with foreign keys, and use the optional `django_assets_brokerage` app (shipped in the same distribution) for ready-made high-level transaction templates.
* Valuation and P&L helpers (PostgreSQL functions and Python APIs).
* Postgres **domains** for common decimal scales (e.g., `dec8`, `dec18`).
* **Generated columns** for scaled integers to optimize filters/sorts.
* **Partitioning templates** for large time-series tables (transaction legs).

### Enforcement & Safety

* DB-level **scale constraints** via `CHECK (scale(col) <= N)` or domains.
* DB-level **balanced transaction constraint** (deferrable trigger).
* Optional **non-negative quantity checks** (prevent short holdings where not allowed).
* All timestamps stored in **UTC** with exchange time zones recorded separately.

### Integration & UX

* Django admin + DRF serializers for instruments and transactions.
* DRF "Measure" field: `{ "amount": "12.3456", "unit": "USD" }`.
* Clear migration templates for domains, triggers, and indexes.
* Optional fixtures for major instruments (USD, BTC, AAPL, ES, SPX options).

## Non-Goals (v1)

* Cross-DB portability (Postgres-only).
* Broker integrations, market data ingestion, or live feeds.
* **Asset price storage**: the system does not store any asset prices (current or historical). Prices are retrieved via connectors that query external APIs. Developers build their own connectors for portfolio valuation purposes.
* Tax or lot-matching logic (FIFO/LIFO modules may arrive later).
* Real-time P&L dashboards (expose data, not visualization).
* Cross-currency balancing (no FX conversions in trigger).

## Target Users

* **Fintech & quant developers** building ledger or portfolio backends.
* **Accounting or custody systems** needing double-entry enforcement.
* **Data engineers** normalizing broker exports into a unified schema.

## Data Model

### Core Tables (Django Models)

| Table          | Purpose                                                           | Model Exposed |
| -------------- | ----------------------------------------------------------------- | ------------- |
| `exchanges`    | Exchange metadata (code, timezone).                               | ✅ Yes        |
| `instruments`  | Core registry of tradable/currency assets.                        | ✅ Yes        |
| `identifiers`  | ISIN/CUSIP/FIGI/OPRA/ticker mappings.                             | ✅ Yes        |
| `accounts`     | Brokerage/bank/wallet accounts.                                   | ✅ Yes        |
| `transactions` | Header record for transaction legs. **Source of truth for holdings.** Stored in double-entry format internally. | ✅ Yes        |
| `transaction_legs`     | Double-entry ledger entries (+/- in native units). Each transaction has balanced transaction legs per instrument. | ✅ Yes        |
| `holdings`     | **Derived** quantity snapshot per account/instrument (calculated from transaction history, or materialized view for performance). | ✅ Yes        |

**All core tables are exposed as Django models** that developers can:
* Reference with `ForeignKey` relationships
* Query with standard Django ORM (`Transaction.objects.filter(...)`)
* Extend with related models in their own applications

### Extension Tables (per asset type)

`currency_meta`, `crypto_meta`, `equity_meta`, `option_meta`, `future_meta`, `bond_meta` — also exposed as Django models.

### Model Relationships

* `instruments` self-links via `underlying_id` (for derivatives) and `price_currency_id`.
* `transaction_legs` link to `transaction_id`, `account_id`, and `instrument_id` — all ForeignKeys.
* `transactions` have a `account` ForeignKey and `transaction_legs` reverse relationship.
* `Transaction.metadata` — JSONField for flexible developer extensions.
* `Transaction.description` — TextField for human-readable notes.
* `TransactionLeg.metadata` — JSONField for flexible per-leg developer extensions.
* `TransactionLeg.description` — TextField for per-leg human-readable notes.

## Precision & Math Rules

* **Quantization:** per-instrument scale on save.
* **Rounding:** `ROUND_HALF_UP` default.
* **Valuation:** `value = qty × price × multiplier`; output in `price_currency`.
* **Units throughout:** all amounts in transactions and transaction legs use units — currency amounts (USD, EUR, etc.), asset quantities (shares, option contracts, crypto units), fees, and dividends. No implicit conversions or assumptions.
* **Options:** default `multiplier=100` (configurable).
* **Crypto:** 8–18 decimals typical; enforced in DB and Python.
* **Domains:** `dec8`, `dec18`, etc., for reusable `NUMERIC` scales.

## Integrity Enforcement

### Scale Check (domain or check constraint)

```sql
CREATE DOMAIN dec8 AS numeric CHECK (scale(VALUE) <= 8);
```

### Balanced Transaction Legs Trigger

```sql
CREATE OR REPLACE FUNCTION assert_transaction_balanced() RETURNS trigger AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM accounts_transactionleg p
    WHERE p.transaction_id = NEW.transaction_id
    GROUP BY p.instrument_id
    HAVING SUM(p.amount) <> 0
  ) THEN
    RAISE EXCEPTION 'Unbalanced transaction %', NEW.transaction_id;
  END IF;
  RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transaction_legs_balanced
AFTER INSERT OR UPDATE OR DELETE ON accounts_transactionleg
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_transaction_balanced();
```

### Indexes

* `(transaction_id, instrument_id)` for trigger efficiency.
* `(account_id, instrument_id)` on holdings.

## Performance Layer

* **Generated columns**: e.g. `amount_scaled = (amount * 1e8)::bigint`.
* **Partitioning**: `transaction_legs` range-partitioned by month.
* **Btree / BRIN indexes** for time-series scanning.
* **Materialized views** for end-of-day positions (optimization; holdings remain transaction-derived).
* **Time-based portfolio aggregation**: efficient queries to compute holdings at any historical point by filtering and summing transaction legs by timestamp.

## Developer API

### Core Transaction Building

The core package provides low-level primitives for building double-entry transactions. These enforce balanced transaction legs and integrity guarantees.

* `TransactionBuilder` — context manager for building balanced double-entry transactions atomically.
* `Transaction.create(transaction_legs, description=None, timestamp=None)` — create a transaction with balanced transaction legs.
* `TransactionLeg.create(transaction, account, instrument, amount, description=None)` — create a transaction leg entry (debit/credit).

### Query & Utility APIs

**Django ORM access**: All core models support standard Django ORM queries:
* `Transaction.objects.filter(account=my_account)`
* `TransactionLeg.objects.filter(instrument=AAPL)`
* `Account.transactions.all()` (reverse relationship)
* `Transaction.transaction_legs.all()` (reverse relationship)

**Helper methods**:
* `Measure(amount, unit)` type for exact arithmetic.
* `value(qty, price)` helper for valuation calculations.
* `Instrument.resolve(code)` — resolve instrument by ticker, ISIN, CUSIP, etc.
* `Instrument.quantize(amount)` — quantize amount according to instrument's precision rules.
* `Portfolio.at(account, as_of)` — query portfolio composition at a specific date/time by aggregating transactions.
* `Holding.current(account, instrument)` — get current holding quantity.
* `Holding.historical(account, instrument, as_of)` — get historical holding quantity at a specific date/time.
* **Price connectors**: developers build connectors to retrieve asset prices from external APIs. A reference connector implementation is included as a starting point. See `django_assets_core_price_connectors_guide.md` for details.

### Extensibility

**Django-native models**: All core models (`Account`, `Instrument`, `Transaction`, `TransactionLeg`) are standard Django models that developers can:
* Reference with `ForeignKey` relationships from their own models
* Query with standard Django ORM (`Transaction.objects.filter(...)`)
* Extend with relationships (one-to-one, foreign keys, generic relations)

**Extension patterns**: See `django_assets_core_extension_patterns_guide.md` for detailed guides on:
* Metadata on transactions (JSON field and one-to-one patterns)
* Building custom transaction templates using `TransactionBuilder`
* Querying and reporting with Django ORM

**Transaction templates**: High-level transaction templates (e.g., `buy_shares()`, `dividend_paid()`) are provided by the optional `django_assets_brokerage` app shipped in the same distribution. Alternatively, build your own using the core `TransactionBuilder` APIs.

**Price connectors**: The system does not store asset prices. Instead, developers build connectors that retrieve prices from external APIs (market data providers, broker APIs, etc.) for portfolio valuation purposes. A reference connector implementation is included as a starting point. See `django_assets_core_price_connectors_guide.md` for comprehensive documentation on building custom connectors.

## Packaging & Deployment

* PyPI distribution: `django-assets`. Single `pip install django-assets`.
* Three Django apps ship in the distribution:
  - `django_assets_core` — ledger primitive (required)
  - `django_assets_brokerage` — transaction templates (optional)
  - `django_assets_trades` — trade grouping and P&L (optional)
* Adopters add only the apps they want to `INSTALLED_APPS`. Unused apps create no tables and have no side effects.
* Migrations include PL/pgSQL trigger, domains, and indexes.
* Tested on **PostgreSQL ≥ 12**, **Django ≥ 4.2 LTS**, **Python ≥ 3.11**.
* Licensed under AGPLv3.

See ADR-0015 in `adr/` for the rationale behind the monolithic distribution and the cases where separate packages (integration/data add-ons) are appropriate.

### Apps within the distribution

* **`django_assets_core`** — Required. Ledger schema (`Instrument`, `Identifier`, `Account`, `Transaction`, `TransactionLeg`, `Exchange`, optional `OptionMeta`/`Deliverable`/`CorporateAction`/`CurrencyMeta`/`CryptoMeta`), deferred balance trigger, query APIs (`Portfolio.at`, `Holding.current`), `TransactionBuilder` primitive, `Instrument.resolve` machinery.
* **`django_assets_brokerage`** — Optional. High-level transaction templates for brokerage account operations (buy/sell, dividends, corporate actions, options, futures, etc.). See `django_assets_brokerage_requirements.md`.
* **`django_assets_trades`** — Optional. Trade management and tagging, including hierarchical trades, user-defined tag categories, and trade P/L tracking. See `django_assets_trades_requirements.md`.

### Related integration packages (separate PyPI distributions)

Integration and data add-ons live in their own PyPI packages because they have different release cadences and dependency footprints:

- `django-assets-occ-feed` — OCC memo ingestion
- `django-assets-broker-import` — broker statement parsers (Schwab, Fidelity, IB, ...)
- `django-assets-us-corp-actions` — US equity corporate-action ingestion
- `django-assets-fx-rates` — FX rate registry / provider adapter

(These are not all built yet; the list reflects the intended pattern for sibling packages as they emerge.)

### Related Documents

* **`docs/adr/`** — Architecture Decision Records. The `docs/adr/README.md` lists all accepted decisions; `open-questions.md` lists what's still in flight. **ADRs are the source of truth for current design.**
* **`docs/historical/`** — Pre-ADR design and requirements documents preserved for historical context. See `docs/historical/README.md` for status. These predate several architectural decisions and are not authoritative — refer to the ADRs first.

## Testing Scope

* Unit tests for arithmetic, rounding, and quantization.
* DB integration tests:

  * Balanced transaction legs trigger fires correctly.
  * Constraint violations produce clear errors.
  * Domains enforce scale.
  * Transactions can be committed when balanced and rollback when not.
* Performance benchmarks (transaction leg inserts, EOD valuations).
* Sample fixtures for instruments and trades.

## Documentation Scope

* **Getting started:** install, migrate, seed base instruments.
* **Core examples:** building transactions with `TransactionBuilder`, creating balanced transaction legs, querying holdings.
* **Extension patterns:** guide showing all extension patterns (foreign keys, metadata, custom templates).
* **Example applications:** complete example showing Trade model with P/L calculation, reporting queries.
* **Cookbook:** custom transaction templates, handling edge cases, validate balance errors.
* **Django ORM guide:** querying transactions, building reports, using relationships.
* **Admin/DRF examples:** managing instruments, creating transactions via API.
* **SQL guides:** extending domains, adding custom triggers.

## Roadmap (0.1 → 1.0)

| Milestone    | Highlights                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------ |
| **v0.1 MVP** | Core schema, Measure class, Postgres migrations, balanced transaction legs trigger, TransactionBuilder, admin & DRF. |
| **v0.2**     | Query APIs (Portfolio.at, Holding.current), simple valuation helpers.                      |
| **v0.3**     | Import/export utilities, generated columns for scaled ints.                                |
| **v0.4**     | Materialized EOD positions view.                                 |
| **v0.5+**    | Lot tracking, multi-currency valuation helpers, advanced query optimizations.              |

## Success Criteria

* Any mix of instruments (USD, BTC, AAPL, SPX option, ES future, US bond) can be represented precisely.
* Every transaction is **provably balanced** at commit.
* Schema and migrations run cleanly on PostgreSQL with no ORM hacks.
* Developers can reference core models with `ForeignKey` relationships in their own applications.
* Developers can query holdings and transactions using standard Django ORM (`Transaction.objects.filter(...)`).
* Extension patterns (foreign keys, metadata, custom templates) are well-documented and easy to use.
* Unit + integration tests reproduce deterministic P&L across restarts.

## License

Copyright (C) 2025 django-assets contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License** as published by the Free Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/agpl-3.0.html>. The full license text is in [LICENSE](LICENSE).

### What AGPLv3 means for you

* **Using it (including in a SaaS / over a network):** if you run a modified version of this software and let users interact with it over a network, you must offer those users the **complete corresponding source code** of your modified version under AGPLv3.
* **Distributing it:** any derivative work (including projects that link this code as a dependency) must also be licensed under AGPLv3.
* **Internal use without modification:** no source-disclosure obligation.

If AGPLv3 doesn't fit your use case, reach out to discuss a commercial license.
