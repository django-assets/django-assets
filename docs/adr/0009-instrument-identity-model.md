# ADR-0009: Instrument identity tracks legal security, not venue

## Status

Accepted — 2026-06-02

## Context

The package needs to model financial instruments precisely enough to support real-world portfolio scenarios across multiple exchanges and instrument types. The design problem decomposes into four sub-questions:

1. Is a stock that trades on multiple exchanges (e.g., AAPL on NASDAQ and any cross-listing) ONE Instrument or many?
2. How do depositary receipts (ADR, GDR) and Argentine certificates (CEDEAR) relate to their underlying foreign shares? Are they the same Instrument or different ones?
3. How are ticker renames (FISV → FI), exchange transfers (NYSE → NASDAQ, or NASDAQ → NYSE), and delistings represented without orphaning historical transactions?
4. How does the resolver disambiguate symbol collisions across exchanges (e.g., ticker "F" exists on multiple exchanges for different companies)?

Three patterns were considered:

- **Pattern A — Instrument per asset, exchange as metadata.** Simplest. Fails for CEDEAR/ADR conversions because they're legally distinct securities with different issuers and reserves; treating them as the same Instrument as the underlying common stock is incorrect.
- **Pattern B — Instrument per (asset, exchange) listing, composite key.** Fails for "same legal security, different venue" cases: a Fiserv NYSE→NASDAQ relisting in 2020 does not issue new shares, but Pattern B would create a new Instrument row and orphan existing holdings.
- **Pattern D — Instrument per legal security; identifiers (including exchange-scoped tickers) live in a separate Identifier table.** Combines the right properties of A and B: legally distinct securities (CEDEAR vs. common stock) get separate rows; same legal security across venues stays one row; ticker renames and venue changes are updates to identifier rows, not to Instrument identity.

The chosen rule:

> Same legal security, different venues → ONE Instrument row.
> Different legal securities representing the same economic interest → SEPARATE Instrument rows, linked via `underlying`.

Real-world test cases that drove the design:

- **CEDEAR ↔ common stock**: AAPL CEDEAR (BYMA, issued by an Argentine custodian, ARS-denominated) is a different legal security than AAPL common stock (NASDAQ, issued by Apple, USD-denominated). 20 CEDEAR = 1 AAPL share (ratio metadata on `cedear_meta`). DTC transfers between them are real conversion events modeled as four-leg transactions through a virtual conversion account.
- **Fiserv NASDAQ → NYSE (2020), then ticker FISV → FI (2024)**: Same Instrument row throughout. CUSIP and ISIN unchanged. Operations are updates to Identifier rows (deactivate old ticker, add new ticker with `effective_from`) and an update to `Instrument.primary_exchange` and `Instrument.code`. Existing transactions and holdings continue referencing the same Instrument.
- **Delisting from one exchange while continuing to trade elsewhere**: Same Instrument row. Deactivate the old ticker identifier, add the new venue's ticker, update `primary_exchange`. `Instrument.is_active` only flips when no venue lists the security at all.
- **Ticker reuse after delisting** (e.g., a defunct company's ticker reassigned to an unrelated company): two separate Instrument rows. The `is_active` partial unique constraint on Identifier allows both rows to coexist because only one ticker identifier is active at any given time.
- **Corporate actions (mergers, acquisitions, spinoffs)**: handled by `Instrument.successor` (1:1 replacement) and possibly a future `Instrument.origin` FK (M:1 spinoff derivation).

Identifier-level chains (`predecessor_identifier`, `successor_identifier`) were considered and rejected. Date-based history via `effective_from` and `effective_to` already encodes the same information; adding chain FKs would create a dual source of truth that can disagree if one side is forgotten.

## Decision

`Instrument` represents a legal security. Identity is independent of venue. Exchange-scoped tickers and global identifiers (ISIN, CUSIP, FIGI, OPRA, SEDOL) live in the `Identifier` table.

### Instrument (in the `django_assets.core` sub-package)

Per ADR-0020, core's `Instrument` carries only the fields required for numeric integrity and lookup. Categorization (`kind`), reference metadata (`primary_exchange`), and relationship FKs (`underlying`, `successor`) live in sibling sub-package extensions, not in core.

```python
class Instrument(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=64, db_index=True)
    quantity_decimals = models.PositiveSmallIntegerField(default=4)
    price_decimals = models.PositiveSmallIntegerField(default=4)
    multiplier = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    price_currency = models.ForeignKey("self", related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
```

- `code` is a denormalized display convenience and is not unique by itself. Resolution goes through `Identifier`.
- Precision fields (`quantity_decimals`, `price_decimals`, `multiplier`) are numeric structural rules — they govern how amounts are stored and rounded.
- `price_currency` self-FK exists for valuation queries (an asset is priced in some unit). Nullable for base currencies.
- `is_active` is a structural flag; sibling sub-packages may interpret it.
- `metadata` is the JSON escape hatch for any per-instrument data that doesn't deserve a structural column.

### Sibling-app extensions to Instrument

Categorization and relationships live in the `django_assets.brokerage` sub-package's extension models:

- `EquityMeta`, `OptionMeta`, `CurrencyMeta`, `CryptoMeta`, `FutureMeta`, `BondMeta` — per-asset-type metadata with `OneToOneField(Instrument)`.
- `OptionMeta.underlying`, `OptionMeta.expiry`, etc. — derivative relationships and option-specific fields.
- `CorporateAction` linkage — when an instrument is replaced by a successor through a corporate action, the relationship is recorded in brokerage's corporate-action machinery, not on the Instrument row itself.
- `primary_exchange` becomes a field on the per-asset-type metadata or a host-side concern; not on core's Instrument.

Each sibling sub-package decides what relationships matter for its domain.

### Identifier

```python
class Identifier(models.Model):
    instrument = models.ForeignKey(Instrument, related_name="identifiers", on_delete=models.CASCADE)
    type = models.CharField(max_length=20)  # ticker, isin, cusip, figi, opra, sedol
    value = models.CharField(max_length=64)
    exchange = models.ForeignKey("Exchange", null=True, blank=True, on_delete=models.PROTECT)  # null for global identifiers (ISIN, CUSIP, FIGI)
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
```

- The partial unique constraint applies only among `is_active=True` rows. Inactive (historical) identifiers can share values without conflict.
- `exchange` is required for exchange-scoped identifiers (ticker, OPRA) and null for global ones (ISIN, CUSIP, FIGI, SEDOL).
- `effective_from` and `effective_to` capture history. Chains between Identifier rows are NOT modeled — date ordering serves the same purpose.

### Resolver contract

`Instrument.resolve(value, *, type="ticker", exchange=None, as_of=None)`:

1. Filter `Identifier` rows by `type` and `value`.
2. If `exchange` is provided, filter to `exchange=exchange` OR `exchange IS NULL` (global identifiers).
3. If `as_of` is provided, filter to `effective_from <= as_of AND (effective_to IS NULL OR effective_to >= as_of)`. Otherwise filter to `is_active=True`.
4. If 1 match → return its Instrument.
5. If 0 matches → raise `InstrumentNotFoundError`.
6. If multiple matches → raise `AmbiguousInstrumentError`.

The default resolver is host-configurable via `DJANGO_ASSETS_INSTRUMENT_RESOLVER`. See OQ-9 for the default resolver's normalization behavior (still open).

### Time-travel and historical resolution

`Portfolio.at(account, as_of)` aggregates `TransactionLeg` rows that already reference specific Instrument IDs. Symbols do not need re-resolution at historical dates. The `as_of` parameter on `Instrument.resolve` exists for importing legacy data with historical ticker names, not for portfolio queries.

## Consequences

**Easier:**

- Same-security-across-venues works correctly. Fiserv's NYSE↔NASDAQ history is captured without data migration or orphaned holdings.
- CEDEAR / ADR conversions are accurately modeled. DTC transfers between distinct legal securities are real four-leg events with per-instrument balance preserved. The CEDEAR-to-underlying relationship lives in the `django_assets.brokerage` sub-package's metadata extension (e.g., `CedearMeta.underlying`), not on core's Instrument.
- Ticker reuse and ticker rename are different operations. Rename updates identifier rows on the same Instrument; reuse is two separate Instruments. The partial unique constraint lets both work.
- Delisting does not orphan historical data. The Instrument persists; `is_active` only flips when truly no venue lists it.
- Identifier history is queryable by date without chain maintenance.
- Core stays narrowly focused on numeric integrity (per ADR-0020). Categorization and relationships scale through sibling sub-packages.

**Harder:**

- The resolver is more complex than a single-table lookup. `Instrument.resolve` must query `Identifier` and disambiguate. Resolver performance depends on a `(type, value, exchange, is_active)` index on `Identifier`.
- Identifier dates require operational hygiene. When marking an identifier inactive, the operator should also set `effective_to`. The package can ship a helper (`Instrument.rename_identifier(old, new, on=date)`) that does this atomically.
- Symbol ambiguity is a runtime error class. Hosts must handle `AmbiguousInstrumentError` and pass an `exchange` hint when needed.

**Deferred:**

- Corporate-action linkage between Instruments (acquisitions, mergers, spinoffs) — handled by sibling sub-package machinery in the `django_assets.brokerage` sub-package, not by self-FKs on core's Instrument.
- Bitemporal Identifier dating (system-time vs. event-time) — `effective_from`/`effective_to` are event-time only in v0.1. If reconciliation against historical broker statements requires reproducing what an identifier *resolved to* at a past system time, bitemporal columns can be added later as a non-breaking schema extension.

## Related

- ADR-0020 (Core ships only numeric integrity) — the principle that drove the removal of `kind`, `underlying`, `successor`, and `primary_exchange` from core's Instrument.
- ADR-0005, ADR-0006, ADR-0008 cover the Account/User side of the schema.
