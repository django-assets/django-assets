# Extension Pattern 2: Metadata on Transactions and Legs

This pattern shows how to add structured or unstructured metadata to transactions and transaction legs. Two approaches are available: JSON metadata fields (flexible) or one-to-one relationships (queryable).

## Overview

Add metadata to transactions and transaction legs using either:
- **JSON metadata fields**: Flexible, no migrations needed, but not directly queryable
- **One-to-one relationships**: Structured, queryable, but requires migrations

**Use cases:**
- Store trade IDs, strategy names, broker references
- Track settlement dates, exchange information
- Add custom fields without schema changes (JSON)
- Build queryable metadata for reporting (one-to-one)

## Metadata on Transactions

### Option A: JSON Metadata Field

The `Transaction` model includes a `metadata` JSONField for flexible, unstructured data:

```python
from django_assets_core.models import Transaction

# Create transaction with metadata
transaction = Transaction.objects.create(
    account=my_account,
    description="Stock purchase",
    metadata={
        "trade_id": "T123",
        "strategy": "momentum",
        "broker_reference": "BR-456",
        "custom_field": "any_value"
    }
)

# Update metadata
transaction.metadata["broker_fee"] = Decimal('1.00')
transaction.save()
```

**Pros:**
* Flexible — no migrations needed
* Easy to add/remove fields
* Good for ad-hoc metadata

**Cons:**
* Not queryable directly in SQL
* Less structured
* No type validation

### Option B: One-to-One Relationship

Create a structured metadata model with a one-to-one relationship:

```python
from django.db import models
from django_assets_core.models import Transaction

class TransactionMetadata(models.Model):
    """Structured metadata for transactions"""
    transaction = models.OneToOneField(
        Transaction,
        related_name='custom_metadata',
        on_delete=models.CASCADE
    )
    
    # Structured fields
    trade_id = models.CharField(max_length=50, db_index=True)
    strategy = models.CharField(max_length=100)
    broker_reference = models.CharField(max_length=100)
    broker_fee = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    
    class Meta:
        db_table = 'trading_transaction_metadata'
```

**Pros:**
* Fully queryable with Django ORM
* Type-safe and validated
* Can add indexes for performance
* Better for reporting

**Cons:**
* Requires migrations for schema changes
* More structured, less flexible

### Usage Example

```python
# Create transaction
transaction = Transaction.objects.create(
    account=my_account,
    description="Stock purchase"
)

# Add structured metadata
metadata = TransactionMetadata.objects.create(
    transaction=transaction,
    trade_id="T123",
    strategy="momentum",
    broker_reference="BR-456"
)

# Query by metadata
trades = TransactionMetadata.objects.filter(
    strategy="momentum",
    transaction__account=my_account
).select_related('transaction', 'transaction__account')
```

## Metadata on Transaction Legs

Transaction legs support the same metadata and description patterns as transactions, allowing per-leg information storage.

### Option A: JSON Metadata Field

The `TransactionLeg` model includes a `metadata` JSONField for flexible, unstructured per-leg data:

```python
from django_assets_core.models import Transaction, TransactionLeg
from django_assets_core import TransactionBuilder

# Create transaction with leg metadata
with TransactionBuilder(
    account=my_account,
    description="Stock purchase"
) as builder:
    # Cash leg with metadata
    cash_leg = builder.add_transaction_leg(
        account.cash_account,
        instrument.price_currency,
        -total_cost,
        metadata={
            "settlement_date": "2024-01-15",
            "broker_reference": "BR-CASH-123",
            "fee_included": True
        },
        description="Cash payment for purchase"
    )
    
    # Asset leg with metadata
    asset_leg = builder.add_transaction_leg(
        account,
        instrument,
        quantity,
        metadata={
            "settlement_date": "2024-01-17",  # T+2 settlement
            "exchange": "NASDAQ",
            "broker_reference": "BR-ASSET-456"
        },
        description="Shares acquired"
    )
```

**Use cases for leg-level metadata:**
* **Settlement dates**: Different legs may settle on different dates (e.g., cash T+0, shares T+2)
* **Exchange/venue information**: Track which exchange each leg executed on
* **Broker references**: Per-leg confirmation numbers or broker-specific IDs
* **Fee attribution**: Mark which leg represents fees vs. asset vs. cash
* **Tax lot method**: Store FIFO/LIFO preference (though typically stored on transaction)

**Pros:**
* Flexible — no migrations needed
* Per-leg granularity for complex transactions
* Good for settlement dates, exchanges, broker references

**Cons:**
* Not directly queryable in SQL (use structured models if needed)
* Less structured
* No type validation

### Option B: One-to-One Relationship

Create a structured metadata model for transaction legs:

```python
from django.db import models
from django_assets_core.models import TransactionLeg

class TransactionLegMetadata(models.Model):
    """Structured metadata for transaction legs"""
    leg = models.OneToOneField(
        TransactionLeg,
        related_name='custom_metadata',
        on_delete=models.CASCADE
    )
    
    # Structured fields
    settlement_date = models.DateField(null=True, db_index=True)
    exchange = models.CharField(max_length=50, null=True)
    broker_reference = models.CharField(max_length=100, null=True)
    lot_method = models.CharField(max_length=10, choices=[('FIFO', 'FIFO'), ('LIFO', 'LIFO')], null=True)
    
    class Meta:
        db_table = 'trading_transaction_leg_metadata'
```

**Pros:**
* Fully queryable with Django ORM
* Type-safe and validated
* Can add indexes for performance
* Better for reporting and settlement date queries

**Cons:**
* Requires migrations for schema changes
* More structured, less flexible

### Usage Example

```python
from django_assets_core import TransactionBuilder

# Create transaction with leg descriptions and metadata
with TransactionBuilder(
    account=my_account,
    description="Stock purchase with fees"
) as builder:
    # Add cash leg with description
    builder.add_transaction_leg(
        account.cash_account,
        USD,
        -Decimal('1501.00'),  # $1500 shares + $1 fee
        description="Cash payment including commission",
        metadata={"payment_method": "ACH", "settlement": "T+0"}
    )
    
    # Add asset leg with description
    asset_leg = builder.add_transaction_leg(
        account,
        AAPL,
        Decimal('10'),
        description="10 shares of AAPL acquired",
        metadata={"settlement": "T+2", "exchange": "NASDAQ"}
    )
    
    # Add fee leg with description
    builder.add_transaction_leg(
        account.expense_account,
        USD,
        Decimal('1.00'),
        description="Commission fee",
        metadata={"fee_type": "commission", "rate": "0.001"}
    )
```

## Best Practices

1. **Use JSON metadata for flexible, ad-hoc data** that doesn't need to be queried
2. **Use one-to-one models for structured, queryable data** that you'll filter or aggregate
3. **Store settlement dates and exchange info on legs** using `TransactionLeg.metadata` for per-leg information
4. **Use leg descriptions** to clearly identify which leg represents asset vs. cash vs. fees
5. **Index frequently queried fields** in structured metadata models

## Related Documents

* **`django_assets_core_extension_patterns_guide.md`** — Overview of all extension patterns
* **`django_assets_core_extension_pattern_1_foreign_keys.md`** — Foreign key relationships
* **`django_assets_core_extension_pattern_4_querying_reporting.md`** — Querying metadata examples
