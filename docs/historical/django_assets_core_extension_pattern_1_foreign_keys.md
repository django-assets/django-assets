# Extension Pattern 1: Foreign Key Relationships

This pattern shows how to create models in your own application that reference core models with foreign keys. This is the most common extension pattern and allows you to build domain-specific models that link to transactions, accounts, and instruments.

## Overview

The most common pattern: create models in your own application that reference core models with foreign keys.

**Use cases:**
- Custom trade models that group related transactions
- Portfolio models that aggregate accounts
- Strategy models that track trading strategies
- Any domain-specific model that needs to reference core assets

## Basic Example: Reference Core Models

```python
from django.db import models
from django_assets_core.models import Transaction

class Trade(models.Model):
    """Custom trade model that groups related transactions"""
    name = models.CharField(max_length=200, unique=True)
    opening_transaction = models.ForeignKey(
        Transaction,
        related_name='opening_trades',
        on_delete=models.PROTECT
    )
    closing_transaction = models.ForeignKey(
        Transaction,
        related_name='closing_trades',
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )
```

## Detailed Example: Custom Trade Model

```python
# In your Django app (e.g., trading/models.py)
from django.db import models
from django_assets_core.models import Transaction

class Trade(models.Model):
    """Custom trade model that groups related transactions"""
    
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Additional trade-specific fields
    target_price = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    stop_loss = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    notes = models.TextField(blank=True)
    
    def get_transactions(self):
        """Get all transactions for this trade"""
        return Transaction.objects.filter(trade=self)
    
    def calculate_pnl(self):
        """Calculate P/L for this trade"""
        # Implementation would aggregate transaction values
        # This is a simplified example
        transactions = self.get_transactions()
        # Calculate P/L from transactions
        return Decimal('0')  # Placeholder
    
    class Meta:
        db_table = 'trading_trade'

# Update Transaction model to reference Trade
# In your migration or model extension:
# Transaction.add_to_class('trade', models.ForeignKey(
#     Trade, on_delete=models.SET_NULL, null=True, blank=True
# ))
```

## Usage Example

```python
from trading.models import Trade

# Create trade
trade = Trade.objects.create(
    name="2026 AAPL Purchase",
    description="Initial AAPL position",
    target_price=Decimal('160.00'),
    stop_loss=Decimal('145.00')
)

# Assign transactions to trade (via ForeignKey)
opening_transaction.trade = trade
opening_transaction.save()
closing_transaction.trade = trade
closing_transaction.save()

# Calculate P/L
pnl = trade.calculate_pnl()
print(f"Trade P/L: {pnl}")
print(f"Risk/Reward: {trade.calculate_risk_reward()}")
```

## Best Practices

1. **Use `on_delete=models.PROTECT`** for foreign keys to core transactions to prevent accidental deletion
2. **Use descriptive `related_name`** parameters to avoid conflicts
3. **Index foreign key fields** for better query performance
4. **Use `select_related()`** when querying to avoid N+1 queries
5. **Consider reverse relationships** when designing your models

## Related Documents

* **`django_assets_core_extension_patterns_guide.md`** — Overview of all extension patterns
* **`django_assets_core_extension_pattern_2_metadata.md`** — Adding metadata to transactions
* **`django_assets_core_extension_pattern_4_querying_reporting.md`** — Querying and reporting examples
