# Extension Pattern 6: Tax Lot Tracking

This pattern shows how to implement cost basis accounting using FIFO/LIFO/AVG cost methods. Derive prices from transaction legs rather than storing them separately.

## Overview

Tax lot tracking enables cost basis accounting using FIFO/LIFO/AVG cost methods. This pattern shows how to implement tax lot tracking using transaction legs, deriving prices from the double-entry transaction structure rather than storing them separately.

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

## Core Principle: Derive Price from Transaction Legs

**Important**: Never store price per unit on transaction legs. In a double-entry transaction:
* Cash leg: `-total_cost` (USD paid)
* Asset leg: `+quantity` (shares acquired)

Price per unit is calculated as: `price_per_unit = abs(cash_leg.amount) / abs(asset_leg.amount)`

This ensures price is always consistent with the double-entry accounting structure.

## Step 1: Create Tax Lot Models

```python
from django.db import models
from django_assets_core.models import Account, Instrument, TransactionLeg

class TaxLot(models.Model):
    """Represents a tax lot - shares acquired at a specific cost basis"""
    account = models.ForeignKey(Account, on_delete=models.CASCADE, db_index=True)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, db_index=True)
    
    # Acquisition details
    acquisition_transaction_leg = models.ForeignKey(
        TransactionLeg,
        related_name='acquired_lots',
        on_delete=models.PROTECT,
        help_text="The transaction leg that created this lot"
    )
    acquisition_date = models.DateTimeField(db_index=True)
    cost_basis_per_unit = models.DecimalField(max_digits=20, decimal_places=8)
    original_quantity = models.DecimalField(max_digits=20, decimal_places=8)
    remaining_quantity = models.DecimalField(max_digits=20, decimal_places=8)
    
    # Metadata
    lot_id = models.CharField(max_length=100, unique=True, db_index=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        db_table = 'tax_taxlot'
        indexes = [
            models.Index(fields=['account', 'instrument', 'acquisition_date']),
            models.Index(fields=['account', 'instrument', 'remaining_quantity']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(remaining_quantity__gte=0),
                name='remaining_quantity_non_negative'
            ),
        ]

class LotDisposition(models.Model):
    """Links a sale transaction leg to specific tax lots that were disposed"""
    disposition_transaction_leg = models.ForeignKey(
        TransactionLeg,
        related_name='dispositions',
        on_delete=models.PROTECT,
        help_text="The transaction leg that disposed of shares"
    )
    lot = models.ForeignKey(
        TaxLot,
        related_name='dispositions',
        on_delete=models.PROTECT
    )
    quantity_disposed = models.DecimalField(max_digits=20, decimal_places=8)
    sale_price_per_unit = models.DecimalField(max_digits=20, decimal_places=8)
    
    # Calculated fields
    cost_basis = models.DecimalField(max_digits=20, decimal_places=8)
    realized_gain_loss = models.DecimalField(max_digits=20, decimal_places=8)
    
    class Meta:
        db_table = 'tax_lotdisposition'
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity_disposed__gt=0),
                name='quantity_disposed_positive'
            ),
        ]
```

## Step 2: Price Calculation Helpers

```python
from decimal import Decimal
from django_assets_core.models import Transaction

def calculate_price_from_transaction(transaction, asset_instrument):
    """
    Calculate price per unit from transaction legs.
    
    Derives price from the double-entry structure:
    - Find cash leg (currency instrument)
    - Find asset leg (asset instrument)
    - price = |cash_leg| / |asset_leg|
    """
    legs = transaction.transaction_legs.select_related('instrument').all()
    
    # Find the asset leg
    asset_leg = None
    for leg in legs:
        if leg.instrument == asset_instrument:
            asset_leg = leg
            break
    
    if not asset_leg:
        raise ValueError(f"No leg found for instrument {asset_instrument}")
    
    # Find the cash leg (price currency)
    price_currency = asset_instrument.price_currency
    cash_leg = None
    for leg in legs:
        if leg.instrument == price_currency:
            cash_leg = leg
            break
    
    if not cash_leg:
        raise ValueError(f"No cash leg found for currency {price_currency}")
    
    # Calculate price per unit
    # Both amounts should have opposite signs for balanced transaction
    cash_amount = abs(cash_leg.amount)
    asset_amount = abs(asset_leg.amount)
    
    if asset_amount == 0:
        raise ValueError("Asset amount cannot be zero")
    
    price_per_unit = cash_amount / asset_amount
    return price_per_unit

def get_purchase_price(transaction, instrument):
    """Get purchase price from a buy transaction"""
    price = calculate_price_from_transaction(transaction, instrument)
    
    # Verify it's a purchase (asset leg should be positive)
    asset_leg = transaction.transaction_legs.get(instrument=instrument)
    if asset_leg.amount <= 0:
        raise ValueError("This transaction is not a purchase (asset leg is not positive)")
    
    return price

def get_sale_price(transaction, instrument):
    """Get sale price from a sell transaction"""
    price = calculate_price_from_transaction(transaction, instrument)
    
    # Verify it's a sale (asset leg should be negative)
    asset_leg = transaction.transaction_legs.get(instrument=instrument)
    if asset_leg.amount >= 0:
        raise ValueError("This transaction is not a sale (asset leg is not negative)")
    
    return price
```

## Step 3: Lot Creation and Disposition

```python
def create_lot_from_purchase(transaction, instrument):
    """Create a tax lot from a purchase transaction by deriving price from legs"""
    # Get the asset leg
    asset_leg = transaction.transaction_legs.get(instrument=instrument)
    
    if asset_leg.amount <= 0:
        raise ValueError("Can only create lot from purchase (positive asset amount)")
    
    # Calculate price from transaction legs (derived, not stored)
    price_per_unit = get_purchase_price(transaction, instrument)
    
    lot = TaxLot.objects.create(
        account=transaction.account,
        instrument=instrument,
        acquisition_transaction_leg=asset_leg,
        acquisition_date=transaction.timestamp,
        cost_basis_per_unit=price_per_unit,
        original_quantity=asset_leg.amount,
        remaining_quantity=asset_leg.amount,
        lot_id=f"{transaction.id}-{asset_leg.id}"
    )
    return lot

def dispose_lots_fifo(transaction, instrument):
    """Dispose lots using FIFO method, deriving sale price from transaction legs"""
    # Get the asset leg
    asset_leg = transaction.transaction_legs.get(instrument=instrument)
    
    if asset_leg.amount >= 0:
        raise ValueError("Can only dispose from sale (negative asset amount)")
    
    # Calculate sale price from transaction legs (derived, not stored)
    sale_price_per_unit = get_sale_price(transaction, instrument)
    
    quantity_to_dispose = abs(asset_leg.amount)
    open_lots = TaxLot.objects.filter(
        account=transaction.account,
        instrument=instrument,
        remaining_quantity__gt=0
    ).order_by('acquisition_date')  # FIFO: oldest first
    
    dispositions = []
    remaining_to_dispose = quantity_to_dispose
    
    for lot in open_lots:
        if remaining_to_dispose <= 0:
            break
        
        quantity_from_this_lot = min(lot.remaining_quantity, remaining_to_dispose)
        cost_basis = quantity_from_this_lot * lot.cost_basis_per_unit
        realized_gain_loss = (sale_price_per_unit - lot.cost_basis_per_unit) * quantity_from_this_lot
        
        disposition = LotDisposition.objects.create(
            disposition_transaction_leg=asset_leg,
            lot=lot,
            quantity_disposed=quantity_from_this_lot,
            sale_price_per_unit=sale_price_per_unit,
            cost_basis=cost_basis,
            realized_gain_loss=realized_gain_loss
        )
        dispositions.append(disposition)
        
        # Update lot remaining quantity
        lot.remaining_quantity -= quantity_from_this_lot
        lot.save()
        
        remaining_to_dispose -= quantity_from_this_lot
    
    if remaining_to_dispose > 0:
        raise ValueError(f"Not enough open lots to dispose {quantity_to_dispose} shares")
    
    return dispositions

def dispose_lots_lifo(transaction, instrument):
    """Dispose lots using LIFO method"""
    # Similar to FIFO, but order by acquisition_date descending
    asset_leg = transaction.transaction_legs.get(instrument=instrument)
    
    if asset_leg.amount >= 0:
        raise ValueError("Can only dispose from sale (negative asset amount)")
    
    sale_price_per_unit = get_sale_price(transaction, instrument)
    quantity_to_dispose = abs(asset_leg.amount)
    
    open_lots = TaxLot.objects.filter(
        account=transaction.account,
        instrument=instrument,
        remaining_quantity__gt=0
    ).order_by('-acquisition_date')  # LIFO: newest first
    
    dispositions = []
    remaining_to_dispose = quantity_to_dispose
    
    for lot in open_lots:
        if remaining_to_dispose <= 0:
            break
        
        quantity_from_this_lot = min(lot.remaining_quantity, remaining_to_dispose)
        cost_basis = quantity_from_this_lot * lot.cost_basis_per_unit
        realized_gain_loss = (sale_price_per_unit - lot.cost_basis_per_unit) * quantity_from_this_lot
        
        disposition = LotDisposition.objects.create(
            disposition_transaction_leg=asset_leg,
            lot=lot,
            quantity_disposed=quantity_from_this_lot,
            sale_price_per_unit=sale_price_per_unit,
            cost_basis=cost_basis,
            realized_gain_loss=realized_gain_loss
        )
        dispositions.append(disposition)
        
        lot.remaining_quantity -= quantity_from_this_lot
        lot.save()
        
        remaining_to_dispose -= quantity_from_this_lot
    
    if remaining_to_dispose > 0:
        raise ValueError(f"Not enough open lots to dispose {quantity_to_dispose} shares")
    
    return dispositions
```

## Step 4: Integration Example

```python
from django_assets_core import TransactionBuilder
from decimal import Decimal

# Create a purchase transaction
with TransactionBuilder(
    account=my_account,
    description="Buy 10 shares of AAPL"
) as builder:
    # Add cash leg (what was paid)
    builder.add_transaction_leg(
        account.cash_account,
        USD,
        -Decimal('1501.00'),  # $1500 for shares + $1 fee
        description="Cash payment",
        metadata={"settlement": "T+0"}
    )
    
    # Add asset leg (what was acquired)
    asset_leg = builder.add_transaction_leg(
        account,
        AAPL,
        Decimal('10'),
        description="10 shares acquired",
        metadata={"settlement": "T+2", "exchange": "NASDAQ"}
    )
    
    # Add fee leg (if tracked separately)
    builder.add_transaction_leg(
        account.expense_account,
        USD,
        Decimal('1.00'),
        description="Commission fee"
    )

transaction = builder.transaction

# Create tax lot from purchase (price derived from legs)
lot = create_lot_from_purchase(transaction, AAPL)
# Price = $1500 / 10 shares = $150 per share (fee handled separately)

# Later, sell 5 shares
with TransactionBuilder(
    account=my_account,
    description="Sell 5 shares of AAPL"
) as builder:
    builder.add_transaction_leg(
        account.cash_account,
        USD,
        Decimal('750.00'),  # $150 per share * 5 shares
        description="Cash proceeds"
    )
    
    sale_leg = builder.add_transaction_leg(
        account,
        AAPL,
        -Decimal('5'),  # Negative: shares sold
        description="5 shares sold"
    )

sale_transaction = builder.transaction

# Dispose lots using FIFO
dispositions = dispose_lots_fifo(sale_transaction, AAPL)
# Creates LotDisposition linking sale_leg to the original lot
# Realized gain/loss = (sale_price - cost_basis) * quantity
```

## Best Practices

1. **Always derive price from legs**: Never store price separately - calculate it from cash and asset legs
2. **Handle fees appropriately**: Fees can be separate legs or included in cash leg amount - be consistent
3. **Use leg descriptions**: Mark which leg is asset vs. cash vs. fee for clarity
4. **Store settlement dates on legs**: Use `TransactionLeg.metadata` for settlement dates if needed
5. **Validate lot quantities**: Ensure dispositions don't exceed available lots
6. **Index lot queries**: Add indexes on `(account, instrument, acquisition_date)` for FIFO/LIFO queries
7. **Use constraints**: Add database constraints to ensure data integrity (e.g., non-negative remaining quantity)

## Related Documents

* **`django_assets_core_extension_patterns_guide.md`** — Overview of all extension patterns
* **`django_assets_core_extension_pattern_3_transaction_templates.md`** — Building transaction templates
* **`django_assets_core_extension_pattern_2_metadata.md`** — Adding metadata to transaction legs
