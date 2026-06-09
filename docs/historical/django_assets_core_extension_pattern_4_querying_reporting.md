# Extension Pattern 4: Querying and Reporting

This pattern shows how to use standard Django ORM to build powerful queries and reports. Combine core models with your extension models for comprehensive analysis.

## Overview

Use standard Django ORM to build powerful queries and reports.

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

## Example: Trade P/L Report

```python
from django.db.models import Sum, Q
from django_assets_core.models import Transaction, TransactionLeg
from trading.models import Trade

def get_trade_pnl_report(account, start_date, end_date):
    """Generate P/L report for closed trades in date range"""
    
    # Get all closed trades in date range
    trades = Trade.objects.filter(
        account=account,
        closing_transaction__isnull=False,
        exit_date__gte=start_date,
        exit_date__lte=end_date
    ).select_related(
        'opening_transaction',
        'closing_transaction',
        'instrument'
    )
    
    # Calculate P/L for each trade
    results = []
    total_pnl = Decimal('0')
    
    for trade in trades:
        pnl = trade.calculate_pnl()
        if pnl:
            total_pnl += pnl
            results.append({
                'trade_id': trade.trade_id,
                'instrument': trade.instrument.code,
                'entry_price': trade.entry_price,
                'exit_price': trade.exit_price,
                'pnl': pnl,
                'strategy': trade.strategy
            })
    
    return {
        'trades': results,
        'total_pnl': total_pnl,
        'count': len(results)
    }
```

## Example: Transaction Analysis

```python
def analyze_transactions(account, instrument, start_date, end_date):
    """Analyze all transactions for an instrument"""
    
    # Get all transactions in date range
    transactions = Transaction.objects.filter(
        account=account,
        timestamp__gte=start_date,
        timestamp__lte=end_date
    ).prefetch_related(
        'transaction_legs',
        'transaction_legs__instrument'
    )
    
    # Filter to transactions involving the instrument
    instrument_transactions = []
    for transaction in transactions:
        legs = transaction.transaction_legs.filter(instrument=instrument)
        if legs.exists():
            total_amount = sum(p.amount for p in legs)
            instrument_transactions.append({
                'transaction_id': transaction.id,
                'timestamp': transaction.timestamp,
                'description': transaction.description,
                'amount': total_amount,
                'type': 'buy' if total_amount > 0 else 'sell'
            })
    
    return instrument_transactions
```

## Example: Portfolio Holdings Query

```python
def get_portfolio_summary(account, as_of_date):
    """Get portfolio summary using core APIs + custom queries"""
    
    from django_assets_core import Portfolio
    
    # Use core API for holdings
    portfolio = Portfolio.at(account, as_of=as_of_date)
    
    # Initialize price connector (use your custom connector here)
    from django_assets_core.connectors import ExampleHTTPConnector
    from django.conf import settings
    connector = ExampleHTTPConnector(api_key=settings.EXAMPLE_API_KEY)
    
    # Add custom analysis
    summary = []
    for instrument, quantity in portfolio.items():
        # Get latest price via connector
        try:
            latest_price = connector.get_price(instrument, as_of=as_of_date)
            value = quantity * latest_price * instrument.multiplier
        except Exception:
            latest_price = None
            value = Decimal('0')
        
        # Query associated trades
        trades = Trade.objects.filter(
            account=account,
            instrument=instrument,
            entry_date__lte=as_of_date
        ).select_related('closing_transaction')
        
        summary.append({
            'instrument': instrument.code,
            'quantity': quantity,
            'price': latest_price,
            'value': value,
            'open_trades': trades.filter(closing_transaction__isnull=True).count()
        })
    
    return summary
```

## Example: Querying with Metadata

```python
from trading.models import TransactionMetadata

def get_strategy_performance(strategy, start_date, end_date):
    """Get performance metrics for a specific strategy"""
    
    # Query transactions by metadata
    transactions = TransactionMetadata.objects.filter(
        strategy=strategy,
        transaction__timestamp__gte=start_date,
        transaction__timestamp__lte=end_date
    ).select_related(
        'transaction',
        'transaction__account'
    ).prefetch_related(
        'transaction__transaction_legs',
        'transaction__transaction_legs__instrument'
    )
    
    # Aggregate metrics
    total_trades = transactions.count()
    total_value = Decimal('0')
    
    for metadata in transactions:
        transaction = metadata.transaction
        # Calculate transaction value from legs
        # ... implementation ...
    
    return {
        'strategy': strategy,
        'total_trades': total_trades,
        'total_value': total_value,
        'period': (start_date, end_date)
    }
```

## Example: Settlement Date Queries

```python
from trading.models import TransactionLegMetadata

def get_pending_settlements(account, as_of_date):
    """Get all transactions with legs that haven't settled yet"""
    
    # Query legs with settlement dates in the future
    pending_legs = TransactionLegMetadata.objects.filter(
        leg__transaction__account=account,
        settlement_date__gt=as_of_date
    ).select_related(
        'leg',
        'leg__transaction',
        'leg__instrument'
    )
    
    # Group by transaction
    pending_transactions = {}
    for leg_metadata in pending_legs:
        transaction = leg_metadata.leg.transaction
        if transaction.id not in pending_transactions:
            pending_transactions[transaction.id] = {
                'transaction': transaction,
                'pending_legs': []
            }
        pending_transactions[transaction.id]['pending_legs'].append(leg_metadata)
    
    return list(pending_transactions.values())
```

## Best Practices

1. **Use `select_related()`** for foreign key relationships to avoid N+1 queries
2. **Use `prefetch_related()`** for reverse foreign keys and many-to-many relationships
3. **Index frequently queried fields** in your extension models
4. **Combine core models with extension models** for comprehensive analysis
5. **Use `Portfolio.at()`** for holdings queries rather than manually calculating
6. **Cache expensive queries** when appropriate
7. **Use aggregation functions** (`Sum`, `Count`, `Avg`) for performance
8. **Filter early** in the query chain to reduce data processed

## Related Documents

* **`django_assets_core_extension_patterns_guide.md`** — Overview of all extension patterns
* **`django_assets_core_extension_pattern_1_foreign_keys.md`** — Foreign key relationships
* **`django_assets_core_extension_pattern_2_metadata.md`** — Querying metadata examples
* **`django_assets_core_price_connectors_guide.md`** — Using price connectors for valuations
