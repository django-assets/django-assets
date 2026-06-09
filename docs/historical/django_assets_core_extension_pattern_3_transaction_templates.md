# Extension Pattern 3: Custom Transaction Templates

This pattern shows how to build high-level APIs using `TransactionBuilder` for domain-specific operations. Create reusable functions that encapsulate common transaction patterns.

## Overview

Create your own high-level APIs using `TransactionBuilder` for domain-specific operations.

**Note**: The optional `django_assets_brokerage` app (shipped in the same `django-assets` distribution; see ADR-0015) provides pre-built transaction templates for common brokerage operations. This pattern shows how to build your own custom templates for specialized use cases or domain-specific operations not covered by the brokerage app.

**Use cases:**
- Custom buy/sell functions with specific fee handling
- Dividend recording with tax withholding
- Complex multi-leg transactions
- Domain-specific transaction types

## Example: Custom Buy Function

```python
from decimal import Decimal
from django_assets_core import TransactionBuilder
from django_assets_core.models import Account, Instrument

def custom_buy_with_fees(account, instrument, quantity, price, commission):
    """
    Custom buy function that handles commission as a separate expense.
    
    Creates a balanced double-entry transaction:
    - Debit: Cash (account cash account)
    - Credit: Shares (account holdings)
    - Debit: Commission expense (account expense account)
    """
    with TransactionBuilder(
        account=account,
        description=f"Buy {quantity} {instrument.code} @ {price}"
    ) as builder:
        total_cost = quantity * price + commission
        
        # Debit: cash (reduce cash account)
        builder.add_transaction_leg(
            account.cash_account,
            instrument.price_currency,
            -total_cost
        )
        
        # Credit: shares (increase holdings)
        builder.add_transaction_leg(
            account,
            instrument,
            quantity
        )
        
        # Debit: commission expense (if expense account exists)
        if hasattr(account, 'expense_account'):
            builder.add_transaction_leg(
                account.expense_account,
                instrument.price_currency,
                commission
            )
    
    # Transaction is automatically validated and saved
    return builder.transaction
```

## Example: Dividend with Tax Withholding

```python
def dividend_with_tax(account, instrument, gross_amount, tax_withheld):
    """
    Record dividend with tax withholding.
    
    Creates balanced double-entry transaction:
    - Debit: Cash account (net dividend received)
    - Debit: Tax expense (tax withheld)
    - Credit: Dividend income (gross amount)
    """
    net_amount = gross_amount - tax_withheld
    
    with TransactionBuilder(
        account=account,
        description=f"Dividend {instrument.code} (tax withheld)"
    ) as builder:
        # Credit: dividend income (gross)
        builder.add_transaction_leg(
            account.income_account,
            instrument.price_currency,
            gross_amount
        )
        
        # Debit: cash account (net received)
        builder.add_transaction_leg(
            account.cash_account,
            instrument.price_currency,
            net_amount
        )
        
        # Debit: tax expense
        builder.add_transaction_leg(
            account.tax_expense_account,
            instrument.price_currency,
            tax_withheld
        )
    
    return builder.transaction
```

## Example: Complex Multi-Leg Transaction

```python
def margin_interest_payment(account, interest_amount, principal_payment):
    """
    Record margin interest payment with principal reduction.
    
    Creates balanced double-entry transaction:
    - Debit: Interest expense
    - Debit: Margin liability (principal reduction)
    - Credit: Cash account
    """
    total_payment = interest_amount + principal_payment
    
    with TransactionBuilder(
        account=account,
        description="Margin interest and principal payment"
    ) as builder:
        # Credit: cash (payment made)
        builder.add_transaction_leg(
            account.cash_account,
            account.base_currency,
            -total_payment
        )
        
        # Debit: interest expense
        builder.add_transaction_leg(
            account.expense_account,
            account.base_currency,
            interest_amount
        )
        
        # Debit: margin liability (principal reduction)
        builder.add_transaction_leg(
            account.margin_account,
            account.base_currency,
            principal_payment
        )
    
    return builder.transaction
```

## Best Practices

1. **Always validate** that your custom templates create balanced transactions
2. **Use descriptive transaction descriptions** to make transactions easy to understand
3. **Handle edge cases** (e.g., optional accounts, zero amounts)
4. **Return the transaction** so callers can add metadata or link to other models
5. **Use `TransactionBuilder` context manager** to ensure proper validation and saving
6. **Add leg descriptions** to clearly identify each leg's purpose
7. **Consider adding metadata** to transactions for additional context

## Combining with Other Patterns

Custom transaction templates work well with other patterns:

```python
def create_trade_with_metadata(account, instrument, quantity, price, commission, strategy):
    """Create a buy transaction and link it to a trade with metadata"""
    # Pattern 3: Custom transaction template
    transaction = custom_buy_with_fees(account, instrument, quantity, price, commission)
    
    # Pattern 1: Create trade model (foreign key)
    from trading.models import Trade
    trade = Trade.objects.create(
        opening_transaction=transaction,
        name=f"{instrument.code} {transaction.timestamp.date()}"
    )
    
    # Pattern 2: Add metadata (one-to-one)
    from trading.models import TransactionMetadata
    TransactionMetadata.objects.create(
        transaction=transaction,
        trade_id=trade.id,
        strategy=strategy
    )
    
    return trade
```

## Related Documents

* **`django_assets_core_extension_patterns_guide.md`** — Overview of all extension patterns
* **`django_assets_core_extension_pattern_2_metadata.md`** — Adding metadata to transactions
* **`django_assets_core_extension_pattern_6_tax_lot_tracking.md`** — Tax lot tracking (uses transaction templates)
