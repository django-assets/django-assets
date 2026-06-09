# Transaction Templates System Requirements for django_assets_brokerage

## Overview

This document specifies the requirements for `django_assets_brokerage`, an **optional Django app** shipped in the `django-assets` PyPI distribution. It extends `django_assets_core` with high-level transaction templates for brokerage account operations. The app provides convenient APIs for common brokerage operations while maintaining double-entry integrity.

**PyPI distribution**: `django-assets` (single install ships core, brokerage, and trades apps; see ADR-0015)
**Django app**: `django_assets_brokerage`
**Depends on**: `django_assets_core` (also in the same distribution)

### App Structure

`django_assets_brokerage` is an **optional Django app** for use alongside `django_assets_core`. It provides:

- High-level transaction template functions (e.g., `buy_shares()`, `dividend_paid()`)
- Convenient APIs that abstract away double-entry complexity
- Pre-built templates for common brokerage operations
- Automatic handling of fees, taxes, and adjustments

The app is **completely optional** — `django_assets_core` can be used without it. When enabled in `INSTALLED_APPS`, it provides convenience functions that use the core app's `TransactionBuilder` internally to create balanced double-entry transactions.

## Mission Statement

`django_assets_brokerage` provides **high-level transaction templates** that abstract away double-entry complexity for brokerage account operations. Built on top of `django_assets_core`, it allows developers to use simple APIs like `buy_shares()` or `dividend_paid()` without understanding double-entry transaction legs, while ensuring all transactions are stored with double-entry integrity.

## Core Principles

* **Template-based API**: developers interact through high-level transaction templates (e.g., `buy_shares()`, `dividend_paid()`) without needing to understand double-entry bookkeeping or transaction legs.
* **Simple by default**: single-entry style APIs (e.g., `deposit_currency(account, amount, currency)`) that handle all double-entry logic automatically.
* **Double-entry under the hood**: all templates use `django_assets_core`'s `TransactionBuilder` to create balanced double-entry transactions internally.
* **Units throughout**: all amounts use units — currency amounts (USD, EUR, etc.), share quantities, option contracts, crypto units, etc.
* **Extensible**: developers can build custom templates using the same `TransactionBuilder` primitives.

## Goals

### Transaction Templates

High-level transaction templates covering common brokerage operations:

#### Cash Management

* `deposit_currency(account, amount, currency)` — cash deposit
* `withdraw_currency(account, amount, currency)` — cash withdrawal
* `transfer_currency(account_from, account_to, amount, currency)` — inter-account currency transfer
* `interest_earned(account, amount, currency)` — cash balance interest earned
* `interest_charged(account, amount, currency)` — margin/loan interest charged

#### Account Transfers

* `transfer_asset(account_from, account_to, instrument, quantity, fee=None, transfer_type='DTC')` — transfer shares/positions between accounts (DTC, ACAT, etc.)
* `transfer_full_account(account_from, account_to, instruments_and_quantities, cash_amounts, fee=None, transfer_type='ACAT')` — full account transfer (ACAT) with multiple positions
* `transfer_partial_position(account_from, account_to, instrument, quantity, fee=None)` — transfer partial position between accounts
* `transfer_option_position(account_from, account_to, option_instrument, contracts, fee=None)` — transfer option contracts between accounts
* `transfer_future_position(account_from, account_to, future_instrument, contracts, fee=None)` — transfer futures contracts between accounts

#### Equity Trades

* `buy_shares(account, instrument, quantity, price, fee=None)` — purchase shares
* `sell_shares(account, instrument, quantity, price, fee=None)` — sell shares
* `short_shares(account, instrument, quantity, price, fee=None)` — short sale (borrow and sell)
* `cover_shares(account, instrument, quantity, price, fee=None)` — cover short position

#### Dividends & Distributions

* `dividend_paid(account, instrument, amount, fee=None)` — cash dividend
* `dividend_paid_with_tax(account, instrument, amount, tax_withheld, fee=None)` — dividend with tax withholding
* `foreign_dividend_paid(account, instrument, amount, foreign_tax, fee=None)` — foreign dividend with foreign tax
* `dividend_reinvested(account, instrument, cash_amount, shares_received, fee=None)` — dividend reinvestment (DRIP)
* `capital_gain_distribution(account, instrument, amount, fee=None)` — capital gains distribution

#### Corporate Actions

* `stock_split(account, instrument, ratio)` — forward split (e.g., 2:1, 3:2)
* `reverse_split(account, instrument, ratio)` — reverse split (e.g., 1:5)
* `stock_dividend(account, instrument, shares_received)` — stock dividend (paid in shares)
* `spinoff(account, parent_instrument, spinoff_instrument, spinoff_quantity, ratio)` — corporate spinoff
* `merger_acquisition(account, target_instrument, acquirer_instrument, exchange_ratio)` — merger/acquisition
* `rights_offering(account, instrument, rights_received, subscription_price=None)` — rights offering
* `warrant_exercise(account, warrant_instrument, exercise_price, shares_received)` — warrant exercise

#### ADR & Custody Fees

* `adr_fee_deducted(account, instrument, amount)` — ADR custody fee
* `foreign_custody_fee(account, instrument, amount)` — foreign custody fee

#### Options Trading

* `buy_option(account, option_instrument, contracts, premium, fee=None)` — purchase option contract
* `sell_option(account, option_instrument, contracts, premium, fee=None)` — write/sell option contract
* `exercise_option(account, option_instrument, exercise_price, shares_received, fee=None)` — exercise long option
* `assign_option(account, option_instrument, exercise_price, shares_delivered, fee=None)` — assignment on short option
* `expire_option(account, option_instrument, contracts, value)` — option expiration (expires worthless or ITM)

#### Futures Trading

* `buy_future(account, future_instrument, contracts, price, fee=None)` — open long futures position
* `sell_future(account, future_instrument, contracts, price, fee=None)` — open short futures position
* `future_settlement(account, future_instrument, settlement_price, mark_to_market)` — daily mark-to-market or final settlement
* `roll_future(account, old_future, new_future, contracts, roll_price)` — roll futures position

#### Crypto & Digital Assets

* `deposit_crypto(account, instrument, quantity, fee=None)` — crypto deposit
* `withdraw_crypto(account, instrument, quantity, fee=None)` — crypto withdrawal
* `buy_crypto(account, crypto_instrument, quantity, price, currency, fee=None)` — purchase cryptocurrency
* `sell_crypto(account, crypto_instrument, quantity, price, currency, fee=None)` — sell cryptocurrency
* `staking_reward(account, instrument, quantity)` — staking rewards received
* `airdrop(account, instrument, quantity)` — airdrop received
* `hard_fork(account, old_instrument, new_instrument, quantity)` — hard fork split

#### Fees & Charges

* `commission_charged(account, amount, currency, description=None)` — trading commission
* `account_fee(account, amount, currency, description=None)` — account maintenance fee
* `transfer_fee(account, amount, currency, description=None)` — asset transfer fee
* `wire_fee(account, amount, currency)` — wire transfer fee
* `regulatory_fee(account, amount, currency, fee_type)` — SEC, FINRA, or other regulatory fees
* `inactivity_fee(account, amount, currency)` — account inactivity fee

#### Adjustments & Corrections

* `quantity_adjustment(account, instrument, quantity_delta, reason)` — correct quantity errors
* `price_adjustment(account, instrument, price_delta, reason)` — correct price errors
* `account_adjustment(account, instrument, amount_delta, reason)` — general accounting adjustment

#### Tax & Withholding

* `tax_withholding(account, amount, currency, tax_type, description=None)` — tax withheld
* `foreign_tax_withholding(account, amount, currency, country, description=None)` — foreign tax withheld
* `tax_refund(account, amount, currency, description=None)` — tax refund received

## Non-Goals

* Core ledger functionality (provided by `django_assets_core`).
* Market data or pricing services.
* Tax calculation or lot-matching logic.
* Order management or execution systems.

## Target Users

* **Brokerage platform developers** building account management systems.
* **Fintech developers** integrating broker data into their applications.
* **Data engineers** normalizing broker transaction exports.

## Data Model

This app does not add new models to the database. It provides template functions that use the core app's models (`Account`, `Instrument`, `Transaction`, `TransactionLeg`) to create transactions.

All templates use `django_assets_core`'s `TransactionBuilder` internally to create balanced double-entry transactions.

## API Design

### Template Structure

All templates:
1. Accept simple parameters (account, instrument, amounts, quantities).
2. Use `django_assets_core`'s `TransactionBuilder` internally to create balanced double-entry transactions.
3. Return the created `Transaction` object.
4. Handle fees, taxes, and other adjustments automatically.

### Example Usage

```python
from django_assets_brokerage import buy_shares, dividend_paid

# Buy shares - double-entry handled automatically
transaction = buy_shares(
    account=my_account,
    instrument=AAPL,
    quantity=Decimal('10'),
    price=Decimal('150.00'),
    fee=Decimal('1.00')
)

# Dividend with tax withholding
transaction = dividend_paid_with_tax(
    account=my_account,
    instrument=AAPL,
    amount=Decimal('5.00'),
    tax_withheld=Decimal('0.75'),
    fee=None
)
```

### Error Handling

Templates validate inputs and raise clear errors if:
* Account or instrument doesn't exist.
* Quantities or amounts are invalid (negative when not allowed, precision violations).
* Transaction would be unbalanced (should never happen, but fails fast).

## Integration Requirements

### Django Admin Integration

- Templates can be used in admin custom actions or management commands
- Transactions created via templates appear in standard Django admin for `Transaction` model
- No special admin configuration required (uses core app's admin)

### DRF (Django REST Framework) Integration

- Templates can be used in API views to create transactions
- Transactions created via templates are serialized using core app's `TransactionSerializer`
- No special serializer configuration required (uses core app's serializers)

## Database Requirements

This app does not require its own database migrations beyond the ones core ships. It uses the core app's database schema.

All templates create transactions using the core app's `Transaction` and `TransactionLeg` models.

## Testing Scope

* Unit tests for each template function:
  * Correct double-entry transaction legs created.
  * Fees and taxes handled correctly.
  * Edge cases (zero amounts, missing optional params).
* Integration tests:
  * Templates work with real `django_assets_core` models.
  * Transactions are properly balanced.
  * Holdings update correctly after template calls.
* Example fixtures: sample transactions for all template types.

## Documentation Scope

* **Getting started:** install `django_assets_brokerage`, basic usage.
* **Template reference:** complete API documentation for all templates.
* **Examples:** common brokerage workflows (opening account, trading, dividends, corporate actions).
* **Extending:** how to build custom templates using core primitives.
* **Cookbook:** handling edge cases, error recovery, batch operations.

## Packaging & Deployment

* PyPI distribution: `django-assets` (single distribution ships `django_assets_core`, `django_assets_brokerage`, and `django_assets_trades` as Django apps; see ADR-0015).
* Django app label: `django_assets_brokerage`.
* Requires the `django_assets_core` app (also enabled in `INSTALLED_APPS`).
* Tested on **PostgreSQL ≥ 12**, **Django ≥ 4.2 LTS**, **Python ≥ 3.11**.
* Licensed under MIT.

### Installation and Setup

**Installation**:
```bash
pip install django-assets
```

**Django Settings**:
```python
INSTALLED_APPS = [
    'django_assets_core',       # required
    'django_assets_brokerage',  # optional transaction templates
    # ... other apps
]
```

**Usage**:
```python
from django_assets_brokerage import buy_shares, dividend_paid

# Use high-level templates
transaction = buy_shares(
    account=my_account,
    instrument=AAPL,
    quantity=Decimal('10'),
    price=Decimal('150.00'),
    fee=Decimal('1.00')
)
```

## Roadmap

| Milestone    | Highlights                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------ |
| **v0.1 MVP** | Core cash & equity templates (deposit, withdraw, buy, sell), basic dividends.          |
| **v0.2**     | Options trading templates, corporate actions (splits, dividends).                          |
| **v0.3**     | Futures templates, account transfers (ACAT, DTC).                                        |
| **v0.4**     | Crypto templates, advanced corporate actions (mergers, spinoffs).                         |
| **v0.5+**    | Batch operations, template customization helpers, extended fee/tax handling.              |

## Success Criteria

* All templates create balanced double-entry transactions.
* Developers can implement common brokerage workflows without understanding transaction legs.
* Templates are easy to extend or override for custom requirements.
* Complete test coverage for all template functions.
* Clear error messages when templates are used incorrectly.

## Relationship to django_assets_core

`django_assets_brokerage` is an **optional Django app** that runs alongside `django_assets_core` (both ship in the `django-assets` PyPI distribution). The brokerage app:

- **Extends** the core app with convenience functions
- **Uses** `django_assets_core`'s `TransactionBuilder` internally
- **Does not modify** core models or functionality
- **Can be enabled/disabled** in `INSTALLED_APPS` without affecting core functionality

Developers can:

1. **Use templates**: enable `django_assets_brokerage` in `INSTALLED_APPS` and use the high-level templates.
2. **Build custom templates**: use `django_assets_core`'s `TransactionBuilder` directly (see `django_assets_core_extension_patterns_guide.md`).
3. **Mix approaches**: use templates for common operations, custom code for specialized needs.

The core app provides all the storage and integrity guarantees; this brokerage app provides convenience and developer ergonomics.

## Example Use Cases

### Use Case 1: Basic Stock Purchase

```python
from django_assets_brokerage import buy_shares

# Buy 10 shares of AAPL at $150.00 with $1.00 commission
transaction = buy_shares(
    account=my_account,
    instrument=AAPL,
    quantity=Decimal('10'),
    price=Decimal('150.00'),
    fee=Decimal('1.00')
)
# Creates balanced double-entry transaction automatically
```

### Use Case 2: Dividend with Tax Withholding

```python
from django_assets_brokerage import dividend_paid_with_tax

# Record dividend of $5.00 with $0.75 tax withheld
transaction = dividend_paid_with_tax(
    account=my_account,
    instrument=AAPL,
    amount=Decimal('5.00'),
    tax_withheld=Decimal('0.75'),
    fee=None
)
# Creates balanced double-entry transaction with income, cash, and tax legs
```

### Use Case 3: Account Transfer

```python
from django_assets_brokerage import transfer_asset

# Transfer 100 shares of AAPL from Account A to Account B
transaction = transfer_asset(
    account_from=account_a,
    account_to=account_b,
    instrument=AAPL,
    quantity=Decimal('100'),
    fee=Decimal('25.00'),
    transfer_type='ACAT'
)
# Creates balanced double-entry transaction for asset transfer
```

### Use Case 4: Options Trading

```python
from django_assets_brokerage import buy_option, exercise_option

# Buy 10 call option contracts
transaction = buy_option(
    account=my_account,
    option_instrument=AAPL_CALL_150,
    contracts=Decimal('10'),
    premium=Decimal('500.00'),
    fee=Decimal('1.00')
)

# Exercise the options
transaction = exercise_option(
    account=my_account,
    option_instrument=AAPL_CALL_150,
    exercise_price=Decimal('150.00'),
    shares_received=Decimal('1000'),
    fee=Decimal('1.00')
)
```
