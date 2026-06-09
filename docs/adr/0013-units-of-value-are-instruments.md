# ADR-0013: All units of value are first-class Instruments

## Status

Accepted — 2026-06-02

## Context

The ledger needs to represent quantities of many different things: shares of stock, contracts of options, units of currency (USD, EUR, JPY, ARS), cryptocurrencies (BTC, ETH), stablecoins (USDC, USDT, DAI), and potentially commodities (gold, oil) in the future. The design decision is whether all of these share the same `Instrument` schema and ledger machinery, or whether currencies and crypto get a "special" treatment — separate tables, separate balance tracking, separate code paths.

This is the question that decides what kind of system `django-assets-core` is.

Two design schools:

- **Currency-as-Instrument (Approach A).** Every unit of value is an `Instrument` row. Cash balances are `Holding`s computed from `TransactionLeg` aggregations, same as stock positions. The per-instrument balance trigger from ADR-0004 applies uniformly across all asset classes. This is the pattern used by trading systems, prime brokerage ledgers, crypto exchanges, FX systems — anywhere where cash is treated as just another commodity with quantity, unit, and market value.

- **Currency-as-special (Approach B).** Cash lives in dedicated columns, a separate `Currency` table, or a separate balance tracking subsystem. Investments and cash sit in different conceptual buckets. This is the pattern used by consumer accounting tools (Mint, YNAB, Quicken) that assume single-currency dominance and prioritize fast cash-balance reads over multi-currency or multi-asset-class flexibility.

`django-assets-core` is positioned in the first camp. The README's design principles already imply this:

> Units-first design: all transaction legs (both currency and asset sides) use units at all times — currency amounts, share quantities, option contracts, crypto units, etc.

And:

> Any mix of instruments (USD, BTC, AAPL, SPX option, ES future, US bond) can be represented precisely.

The supporting use cases push the same direction:

- Multi-currency portfolios (USD + EUR + JPY in a single Interactive Brokers account) are first-class, not special.
- Crypto positions sit alongside equity positions without separate machinery.
- CEDEAR (Argentine certificate of deposit, ARS-denominated, tracks foreign shares) and ADR (US-listed depositary receipt) only work cleanly if the surrounding cash (ARS for CEDEAR, USD for ADR) is also first-class.
- DTC transfers between exchange-distinct securities (per ADR-0009) are uniform per-instrument balanced transactions only if cash is uniform.
- FX conversions between currencies are uniform double-entry transactions only if currencies are first-class.

Approach B fragments at every one of these touchpoints. Approach A unifies. The slower cash-balance reads at scale that approach A introduces are addressable through materialized holdings (already planned for v0.4 per the roadmap) or through host-side denormalization caching when needed.

## Decision

### Every unit of value is an Instrument

All currencies, cryptocurrencies, stablecoins, and any other unit a user might hold or transact are `Instrument` rows. To the ledger, they are indistinguishable from stocks or options — just things with precision rules that balance per-instrument. Concretely:

- **Fiat currencies** (USD, EUR, JPY, ARS, BRL, GBP, CHF, CNY, ...): `Instrument` rows with `quantity_decimals` per the currency (2 for USD/EUR, 0 for JPY).
- **Cryptocurrencies** (BTC, ETH, SOL, DOGE, ...): `Instrument` rows with `quantity_decimals` per the token (8 for BTC, 18 for ETH).
- **Stablecoins** (USDC, USDT, DAI, ...): `Instrument` rows. Their stablecoin-ness and peg are recorded by `CryptoMeta.is_stablecoin = True` and `CryptoMeta.pegged_to = USD_instrument` in `django_assets.brokerage` (per ADR-0020, core does not categorize).
- **Commodities** (gold, oil) when introduced: `Instrument` rows. Same machinery.

Per ADR-0020, core does not carry a `kind` field on Instrument. Categorization labels live in the per-asset-type metadata extensions in `django_assets.brokerage` (CurrencyMeta, CryptoMeta, EquityMeta, etc.). The ledger itself does not need to know what category a unit belongs to — it just enforces balance.

There is no separate `Currency` table. There is no `Account.cash_USD` column. There is no `TransactionLeg.is_cash` flag. There is one schema, one balance trigger, one aggregation pattern.

### Cash balances are Holdings

A user's USD balance is a `Holding` in the USD `Instrument` for their Account, computed by aggregating `TransactionLeg` rows:

```python
Holding.current(account, instrument=USD)  # → Decimal("1234.56")
```

The same query pattern returns AAPL holdings, BTC holdings, option contract holdings. No special-casing in the API.

`Portfolio.at(account, as_of)` returns a dict-like containing all of them:

```python
{
    USD: Decimal("1234.56"),
    EUR: Decimal("987.65"),
    AAPL: Decimal("100"),
    BTC: Decimal("0.5"),
    PFE1_Jan22_40_Call: Decimal("1"),
}
```

Hosts that want to present cash separately in UI (e.g., "Cash: $1,234 / Holdings: $50,000") filter by the presence of the brokerage-side metadata extensions — e.g., `Instrument.objects.filter(Q(currency_meta__isnull=False) | Q(crypto_meta__isnull=False))` — in their view layer. The ledger does not distinguish; per ADR-0020, core has no `kind` discriminator on `Instrument`.

### Per-instrument precision handles the variance

The `Instrument.quantity_decimals` field carries the precision rules:

- USD, EUR, GBP: `quantity_decimals = 2`
- JPY, KRW: `quantity_decimals = 0`
- BTC: `quantity_decimals = 8`
- ETH: `quantity_decimals = 18`
- ARS, BRL: `quantity_decimals = 2`
- Equities: `quantity_decimals = 0` (whole shares) or 8 (fractional shares enabled)

All amounts are stored in a wide-precision `NUMERIC` column (or `dec18`-style domain) and quantized to the instrument's precision at write time. The package's precision helper enforces this uniformly.

### CurrencyMeta and CryptoMeta extension tables live in `django_assets.brokerage`

Per ADR-0020, opinionated per-asset-type metadata is not in core. The metadata extensions live in `django_assets.brokerage`:

```python
# django_assets/brokerage/models.py

class CurrencyMeta(models.Model):
    instrument = models.OneToOneField(
        "django_assets.Instrument", related_name="currency_meta", on_delete=models.CASCADE,
    )
    iso_code = models.CharField(max_length=3, unique=True)  # ISO 4217 alpha-3 (USD, EUR, JPY)
    iso_numeric = models.PositiveSmallIntegerField(null=True, blank=True)  # ISO 4217 numeric (840 for USD)
    symbol = models.CharField(max_length=8, blank=True)  # "$", "€", "¥"
    is_fiat = models.BooleanField(default=True)
    central_bank = models.CharField(max_length=120, blank=True)  # informational

class CryptoMeta(models.Model):
    instrument = models.OneToOneField(
        "django_assets.Instrument", related_name="crypto_meta", on_delete=models.CASCADE,
    )
    symbol = models.CharField(max_length=20)  # "BTC", "ETH", "USDC"
    network = models.CharField(max_length=40, blank=True)  # "bitcoin", "ethereum", "solana"
    contract_address = models.CharField(max_length=128, blank=True)  # for tokens (USDC, USDT, etc.)
    is_stablecoin = models.BooleanField(default=False)
    pegged_to = models.ForeignKey(
        "django_assets.Instrument", null=True, blank=True,
        related_name="pegged_by", on_delete=models.PROTECT,
    )  # for stablecoins, e.g., USDC.pegged_to = USD_instrument
```

Hosts that install only core (no brokerage) work fine — they just don't have ISO codes or symbol metadata. Their currency Instruments are still first-class for balance and integrity purposes; they just lack the human-facing labels until brokerage is installed.

The principle this ADR established — **every unit of value is an Instrument, treated uniformly by the ledger** — remains in core. Only the categorization labels move to brokerage.

### `Instrument.price_currency` is self-FK clean

Per ADR-0009, every Instrument has a `price_currency` FK to another Instrument. With currencies as first-class:

- `AAPL.price_currency = USD_instrument`
- `EUR.price_currency = EUR_instrument` (self-reference, or NULL with documented convention)
- `BTC.price_currency = USD_instrument` (or EUR if the user prefers — host-configurable)
- `AAPL_CEDEAR.price_currency = ARS_instrument`

No special "non-Instrument price unit" handling.

### Cross-currency transactions are explicit multi-leg patterns

Cross-currency conversions never happen implicitly. There is no implicit FX rate, no "convert this cash leg to the target currency at trade time" magic. A EUR→USD swap is recorded explicitly as a four-leg Transaction:

```
Transaction (FX swap, 100 EUR → 110 USD):
  -100 EUR from user_eur_account
  +100 EUR to user_external
  +110 USD to user_usd_account
  -110 USD from user_external
```

Per-instrument balance:
- EUR: `-100 + 100 = 0` ✓
- USD: `+110 - 110 = 0` ✓

The implicit conversion rate is encoded in the leg amounts. If the host wants to record the rate explicitly for audit or analytics, it goes in `Transaction.metadata` (e.g., `{"fx_rate_eur_usd": "1.10"}`).

The brokerage package's helpers do not perform FX. If a user buys AAPL (USD-denominated) from a EUR-funded account, the host must either:

- Pre-convert the EUR to USD with a separate FX Transaction, then call `buy_shares(account_usd, AAPL, ...)`, or
- Use a brokerage helper that explicitly takes `funding_currency=EUR` and decomposes into two Transactions (the FX leg followed by the purchase leg).

This is OQ-8's resolution and is captured here rather than in a separate ADR.

### Universal pattern: deposits, withdrawals, transfers, dividends, fees

All cash flows use the same shape: balanced legs across the user's account and an `external` (or other system) account. Examples:

```
Cash deposit (bank wire of $5,000):
  +$5,000 USD to user_cash
  -$5,000 USD from user_external

Cash dividend ($24 from AAPL):
  +$24 USD to user_cash
  -$24 USD from user_external

Commission charged ($0.50 broker fee):
  -$0.50 USD from user_cash
  +$0.50 USD to user_external

EUR cash interest:
  +€12 EUR to user_eur_account
  -€12 EUR from user_external
```

Same model for every fiat, every crypto, every direction. The balance trigger validates per-instrument across every Transaction without exception.

### Bootstrapping currency Instruments

`django_assets.brokerage` ships seed fixtures for the most common currencies (USD, EUR, GBP, JPY, CHF, ARS, BRL, CAD, AUD, CNY) and common cryptocurrencies (BTC, ETH, USDC, USDT, DAI, SOL, DOGE). Hosts that install brokerage get the seeded Instruments plus the matching `CurrencyMeta` / `CryptoMeta` rows. Hosts that install only core can create their own Instrument rows with the correct `quantity_decimals` (no labels, just numeric precision).

Each seeded currency is created with appropriate `quantity_decimals`, ISO 4217 code, and symbol. The fixtures live in `django_assets/brokerage/fixtures/`.

## Consequences

**Easier:**

- One uniform ledger schema and one balance trigger across every asset class. No "is this a currency?" branches anywhere in core; categorization is the host's concern via brokerage metadata extensions.
- Multi-currency, multi-asset portfolios are first-class. A user holding USD + EUR + BTC + AAPL + PFE1 calls has five `Holding` rows, queried identically.
- Crypto and stablecoins fit without invention. USDC has a `CryptoMeta` row with `is_stablecoin=True` and `pegged_to=USD_instrument`, traded by the ledger exactly like any other Instrument.
- CEDEAR/ADR/foreign-share patterns (per ADR-0009) work because the cash side (ARS for CEDEAR, USD for ADR) is uniform with the asset side.
- FX is the same shape as any other trade: balanced legs through an external account. No FX engine, no implicit rates.
- The package can claim to be "a ledger for any unit of value" without qualification.

**Harder:**

- Cash balance queries aggregate `TransactionLeg` rows rather than read a single column. For accounts with many transactions, this is slower than a denormalized cash balance would be. Mitigation: materialized holdings (v0.4 roadmap), or host-side denormalization caching (a `Holding` table updated by triggers or batch jobs).
- "USD is an Instrument" is a documentation concept that needs explaining once to new users.
- There is no `Account.cash_balance` column for hosts that expect one. Hosts wanting one denormalize per their needs.
- The per-instrument precision rules must be enforced consistently. Forgetting to quantize a JPY amount to 0 decimals or a BTC amount to 8 decimals will be caught by the `dec18`-domain scale check, but the error happens at INSERT time rather than at the host's input-validation layer.

**Deferred:**

- A denormalized `Account.balance_cache` column for hot cash-balance reads. May be added in a future ADR if performance demands it.
- A `commodities` metadata extension for gold, oil, carbon credits. Schema already supports it (Instruments are uniform); introducing a `CommodityMeta` extension in brokerage is a non-breaking future addition.
- A central FX-rate registry (separate package or sibling). Out of scope for the distribution; the package does not record or compute FX rates, only the multi-leg transactions that bake them in implicitly.

## Related

- ADR-0020 (Core ships only numeric integrity) — the principle that moved CurrencyMeta and CryptoMeta to brokerage.
- ADR-0004 establishes the per-instrument balance trigger that this ADR commits to apply uniformly across all Instruments.
- ADR-0009 establishes the Instrument identity model that all units of value share.
- ADR-0011 establishes that core does not own corporate-action ingestion. FX rates, broker-feed cash parsing, and cross-currency conversion data are host concerns.
- OQ-5 and OQ-8 in `open-questions.md` are resolved by this ADR.
