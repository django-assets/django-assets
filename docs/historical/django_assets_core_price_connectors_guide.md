# Price Connectors Guide

### *Building custom connectors to retrieve asset prices from external APIs.*

## Overview

The `django_assets_core` system does not store asset prices. Instead, developers build connectors that retrieve prices from external APIs (market data providers, broker APIs, etc.) for portfolio valuation purposes.

The system ships with a default connector that uses the reference HTTP connector. Developers are free to build their own connectors for their specific use cases.

**Important**: The core system stores only transaction records. Prices are retrieved on-demand via connectors for valuation purposes. Each Django developer is responsible for building connectors appropriate for their use case.

## Table of Contents

1. [Core Concepts](#core-concepts)
2. [Base Connector Interface](#base-connector-interface)
3. [Default Connector: reference HTTP connector](#default-connector-reference-http-connector)
4. [Building Custom Connectors](#building-custom-connectors)
5. [Using Connectors for Portfolio Valuation](#using-connectors-for-portfolio-valuation)
6. [Advanced Patterns](#advanced-patterns)
7. [Best Practices](#best-practices)
8. [Examples](#examples)
9. [Related Documents](#related-documents)
10. [Summary](#summary)

## Core Concepts

### Why Connectors?

The `django_assets_core` system follows a **transaction-first** architecture where transactions are the source of truth. Asset prices are not stored because:

1. **Prices change frequently**: Storing prices would require constant updates and synchronization
2. **Multiple price sources**: Different developers may use different market data providers
3. **On-demand valuation**: Prices are needed for portfolio valuation, not for transaction storage
4. **Flexibility**: Developers can choose price sources appropriate for their use case

### Connector Interface

All price connectors implement a simple interface with a single method: `get_price(instrument, as_of=None)`. This allows:

- **Uniform API**: All connectors work the same way regardless of underlying data source
- **Easy swapping**: Switch between connectors without changing application code
- **Composability**: Build wrapper connectors (caching, rate limiting, fallback) around base connectors

## Base Connector Interface

All price connectors implement a simple interface:

```python
from abc import ABC, abstractmethod
from decimal import Decimal
from django_assets_core.models import Instrument
from datetime import datetime

class PriceConnector(ABC):
    """Base interface for price connectors"""
    
    @abstractmethod
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """
        Retrieve the current or historical price for an instrument.
        
        Args:
            instrument: The instrument to get price for
            as_of: Optional datetime for historical prices. If None, returns current price.
            
        Returns:
            Decimal price in the instrument's price_currency
            
        Raises:
            PriceNotFoundError: If price is not available for the instrument/as_of date
            PriceConnectorError: For API or connection errors
        """
        pass
```

### Exceptions

The connector interface defines two exception types:

```python
class PriceNotFoundError(Exception):
    """Raised when a price is not available for the requested instrument/as_of date"""
    pass

class PriceConnectorError(Exception):
    """Raised for API or connection errors"""
    pass
```

These exceptions are available in `django_assets_core.connectors`:

```python
from django_assets_core.connectors import PriceNotFoundError, PriceConnectorError
```

## Default Connector: reference HTTP connector

The system ships with a default connector that uses the reference HTTP connector:

```python
from django_assets_core.connectors import ExampleHTTPConnector
from django.conf import settings
from datetime import datetime

# Initialize connector (typically configured via Django settings)
connector = ExampleHTTPConnector(
    api_key=settings.EXAMPLE_API_KEY,
    base_url=settings.EXAMPLE_API_URL
)

# Get current price
price = connector.get_price(instrument=AAPL)

# Get historical price
historical_price = connector.get_price(instrument=AAPL, as_of=datetime(2024, 1, 15))
```

### Configuration

Configure the reference HTTP connector connector via Django settings:

```python
# settings.py
EXAMPLE_API_KEY = "your-api-key-here"
EXAMPLE_API_URL = "https://api.marketdata.com/v1"  # Optional, has default
```

## Building Custom Connectors

Developers can build their own connectors for any price source. Here are several examples:

### Example 1: Simple Broker API Connector

```python
from django_assets_core.connectors import PriceConnector, PriceNotFoundError, PriceConnectorError
from decimal import Decimal
import requests
from datetime import datetime

class CustomBrokerConnector(PriceConnector):
    """Custom connector that queries a broker API"""
    
    def __init__(self, broker_api_key: str, base_url: str = "https://api.broker.com/v1"):
        self.api_key = broker_api_key
        self.base_url = base_url
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """Retrieve price from broker API"""
        try:
            # Query broker API
            response = requests.get(
                f"{self.base_url}/prices/{instrument.code}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"date": as_of.isoformat() if as_of else None},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if "price" not in data:
                raise PriceNotFoundError(f"Price not found for {instrument.code}")
            
            return Decimal(str(data["price"]))
        except requests.RequestException as e:
            raise PriceConnectorError(f"API error: {e}") from e
```

### Example 2: Multi-Source Fallback Connector

```python
class FallbackPriceConnector(PriceConnector):
    """Connector that tries multiple sources in order"""
    
    def __init__(self, *connectors):
        self.connectors = connectors
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """Try each connector in order until one succeeds"""
        last_error = None
        
        for connector in self.connectors:
            try:
                return connector.get_price(instrument, as_of)
            except (PriceNotFoundError, PriceConnectorError) as e:
                last_error = e
                continue
        
        # If all connectors fail, raise the last error
        raise PriceConnectorError(f"All connectors failed. Last error: {last_error}") from last_error

# Usage
primary = ExampleHTTPConnector(api_key=settings.EXAMPLE_API_KEY)
fallback = CustomBrokerConnector(api_key=settings.BROKER_API_KEY)
connector = FallbackPriceConnector(primary, fallback)
```

### Example 3: Database-Backed Connector

```python
from django.db import models
from django.utils import timezone

class PriceCache(models.Model):
    """Model to cache prices temporarily (not for long-term storage)"""
    instrument = models.ForeignKey('django_assets_core.Instrument', on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    as_of = models.DateTimeField()
    source = models.CharField(max_length=100)
    cached_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [('instrument', 'as_of')]
        indexes = [
            models.Index(fields=['instrument', 'as_of']),
        ]

class DatabaseCachedConnector(PriceConnector):
    """Connector that caches prices in database with fallback to API"""
    
    def __init__(self, base_connector: PriceConnector, cache_ttl_hours: int = 24):
        self.base_connector = base_connector
        self.cache_ttl_hours = cache_ttl_hours
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """Check cache first, then fall back to API"""
        cache_key = as_of or timezone.now()
        
        # Check cache
        try:
            cached = PriceCache.objects.get(
                instrument=instrument,
                as_of__date=cache_key.date() if as_of else timezone.now().date()
            )
            # Check if cache is still valid
            age = timezone.now() - cached.cached_at
            if age.total_seconds() < (self.cache_ttl_hours * 3600):
                return cached.price
        except PriceCache.DoesNotExist:
            pass
        
        # Cache miss or expired - fetch from API
        price = self.base_connector.get_price(instrument, as_of)
        
        # Update cache
        PriceCache.objects.update_or_create(
            instrument=instrument,
            as_of__date=cache_key.date() if as_of else timezone.now().date(),
            defaults={
                'price': price,
                'as_of': cache_key,
                'source': 'api'
            }
        )
        
        return price
```

### Example 4: Crypto Exchange Connector

```python
import ccxt
from decimal import Decimal

class CryptoExchangeConnector(PriceConnector):
    """Connector for cryptocurrency prices via ccxt library"""
    
    def __init__(self, exchange_name: str = 'binance', api_key: str = None, api_secret: str = None):
        exchange_class = getattr(ccxt, exchange_name)
        config = {'enableRateLimit': True}
        if api_key:
            config['apiKey'] = api_key
        if api_secret:
            config['secret'] = api_secret
        self.exchange = exchange_class(config)
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """Get crypto price from exchange"""
        try:
            # Convert instrument code to exchange symbol format
            # e.g., "BTC/USD" or "BTCUSD"
            symbol = self._format_symbol(instrument.code)
            
            if as_of:
                # For historical prices, use OHLCV data
                ohlcv = self.exchange.fetch_ohlcv(symbol, '1d', since=int(as_of.timestamp() * 1000), limit=1)
                if not ohlcv:
                    raise PriceNotFoundError(f"Historical price not found for {instrument.code}")
                # Use close price
                price = Decimal(str(ohlcv[0][4]))  # OHLCV format: [timestamp, open, high, low, close, volume]
            else:
                # For current price, use ticker
                ticker = self.exchange.fetch_ticker(symbol)
                price = Decimal(str(ticker['last']))
            
            return price
        except ccxt.BaseError as e:
            raise PriceConnectorError(f"Exchange API error: {e}") from e
    
    def _format_symbol(self, code: str) -> str:
        """Format instrument code to exchange symbol format"""
        # Example: "BTC" -> "BTC/USD"
        # This is simplified - adjust based on your instrument codes
        return f"{code}/USD"
```

## Getting Started

### Basic Usage

```python
from django_assets_core.connectors import ExampleHTTPConnector
from django.conf import settings

# Initialize connector
connector = ExampleHTTPConnector(api_key=settings.EXAMPLE_API_KEY)

# Get current price
price = connector.get_price(instrument=AAPL)
print(f"AAPL price: ${price}")

# Get historical price
from datetime import datetime
historical_price = connector.get_price(instrument=AAPL, as_of=datetime(2024, 1, 15))
print(f"AAPL price on 2024-01-15: ${historical_price}")
```

## Using Connectors for Portfolio Valuation

Here are examples of using connectors for portfolio valuation:

### Basic Portfolio Valuation

```python
from django_assets_core import Portfolio
from django_assets_core.connectors import ExampleHTTPConnector
from django.conf import settings
from decimal import Decimal
from datetime import datetime

def calculate_portfolio_value(account, as_of: datetime = None):
    """Calculate total portfolio value using price connector"""
    
    # Get portfolio holdings
    portfolio = Portfolio.at(account, as_of=as_of or datetime.now())
    
    # Initialize price connector
    connector = ExampleHTTPConnector(api_key=settings.EXAMPLE_API_KEY)
    
    # Calculate portfolio value
    total_value = Decimal('0')
    positions = []
    
    for instrument, quantity in portfolio.items():
        if quantity > 0:  # Only value long positions
            try:
                price = connector.get_price(instrument, as_of=as_of)
                value = quantity * price * instrument.multiplier
                total_value += value
                positions.append({
                    'instrument': instrument.code,
                    'quantity': quantity,
                    'price': price,
                    'value': value
                })
            except Exception as e:
                # Log error but continue with other positions
                print(f"Error getting price for {instrument.code}: {e}")
    
    return {
        'total_value': total_value,
        'positions': positions
    }
```

### Portfolio Summary with Price Data

```python
from django_assets_core import Portfolio

def get_portfolio_summary(account, as_of_date, connector):
    """Get portfolio summary using core APIs + price connector"""
    
    # Use core API for holdings
    portfolio = Portfolio.at(account, as_of=as_of_date)
    
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
        
        # Query associated trades (example from extension patterns)
        from trading.models import Trade
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

## Advanced Patterns

### Caching Connector

For high-frequency portfolio valuations, consider caching prices:

```python
from datetime import datetime, timedelta

class CachedPriceConnector(PriceConnector):
    """Wrapper that caches prices for a short duration"""
    
    def __init__(self, base_connector: PriceConnector, cache_ttl: timedelta = timedelta(seconds=60)):
        self.base_connector = base_connector
        self.cache_ttl = cache_ttl
        self._cache = {}
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        cache_key = (instrument.id, as_of.isoformat() if as_of else None)
        
        # Check cache
        if cache_key in self._cache:
            cached_price, cached_time = self._cache[cache_key]
            if datetime.now() - cached_time < self.cache_ttl:
                return cached_price
        
        # Fetch from base connector
        price = self.base_connector.get_price(instrument, as_of)
        
        # Update cache
        self._cache[cache_key] = (price, datetime.now())
        
        return price
```

### Rate-Limited Connector

```python
import time
from threading import Lock

class RateLimitedConnector(PriceConnector):
    """Wrapper that enforces rate limits"""
    
    def __init__(self, base_connector: PriceConnector, max_requests_per_second: float = 10.0):
        self.base_connector = base_connector
        self.max_requests_per_second = max_requests_per_second
        self.min_interval = 1.0 / max_requests_per_second
        self.last_request_time = 0
        self.lock = Lock()
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        with self.lock:
            # Enforce rate limit
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.min_interval:
                time.sleep(self.min_interval - time_since_last)
            
            self.last_request_time = time.time()
        
        return self.base_connector.get_price(instrument, as_of)
```

### Batch Price Connector

For efficiency when fetching many prices:

```python
class BatchPriceConnector(PriceConnector):
    """Connector that fetches multiple prices in one API call"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.prices.com/v1"
    
    def get_prices(self, instruments: list[Instrument], as_of: datetime = None) -> dict[Instrument, Decimal]:
        """Fetch multiple prices in one batch call"""
        symbols = [inst.code for inst in instruments]
        
        response = requests.post(
            f"{self.base_url}/prices/batch",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "symbols": symbols,
                "date": as_of.isoformat() if as_of else None
            },
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        # Map results back to instruments
        result = {}
        for inst in instruments:
            if inst.code in data:
                result[inst] = Decimal(str(data[inst.code]["price"]))
            else:
                raise PriceNotFoundError(f"Price not found for {inst.code}")
        
        return result
    
    def get_price(self, instrument: Instrument, as_of: datetime = None) -> Decimal:
        """Single price fetch (can batch internally if needed)"""
        results = self.get_prices([instrument], as_of)
        return results[instrument]
```

## Best Practices

1. **Handle errors gracefully**: Price APIs may fail or be unavailable. Always handle exceptions and provide fallback behavior.

2. **Cache strategically**: Use caching for high-frequency queries, but be aware of stale price risks. Consider different TTLs for current vs. historical prices.

3. **Respect rate limits**: Implement rate limiting if your price source has API limits. Monitor your usage to avoid hitting limits.

4. **Validate prices**: Ensure prices are reasonable (positive, within expected ranges) before using them. Log suspicious prices for investigation.

5. **Use appropriate timeouts**: Set reasonable timeouts for API calls to avoid hanging requests. Consider retries with exponential backoff for transient failures.

6. **Log price requests**: Track which instruments are being queried for debugging and monitoring. Log errors with context (instrument, date, error type).

7. **Handle missing data**: Some instruments may not have prices available. Design your application to handle missing prices gracefully (skip valuation, use last known price, etc.).

8. **Consider batch operations**: If your API supports batch requests, use them when fetching prices for multiple instruments to reduce API calls.

9. **Test with real data**: Test your connectors with real instruments and various scenarios (weekends, market holidays, missing instruments).

10. **Document your connector**: Clearly document what your connector does, what APIs it uses, rate limits, error handling, and configuration requirements.

## Examples

### Complete Example: Portfolio Valuation Service

```python
from django_assets_core import Portfolio
from django_assets_core.connectors import PriceConnector, PriceNotFoundError, PriceConnectorError
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class PortfolioValuationService:
    """Service for calculating portfolio values using price connectors"""
    
    def __init__(self, price_connector: PriceConnector):
        self.price_connector = price_connector
    
    def calculate_portfolio_value(self, account, as_of: datetime = None) -> dict:
        """Calculate portfolio value with detailed position information"""
        portfolio = Portfolio.at(account, as_of=as_of or datetime.now())
        
        total_value = Decimal('0')
        positions = []
        errors = []
        
        for instrument, quantity in portfolio.items():
            if quantity == 0:
                continue
            
            try:
                price = self.price_connector.get_price(instrument, as_of)
                
                # Validate price
                if price <= 0:
                    logger.warning(f"Invalid price {price} for {instrument.code}")
                    errors.append({
                        'instrument': instrument.code,
                        'error': 'Invalid price (non-positive)'
                    })
                    continue
                
                value = quantity * price * instrument.multiplier
                total_value += value
                
                positions.append({
                    'instrument': instrument.code,
                    'quantity': str(quantity),
                    'price': str(price),
                    'value': str(value),
                    'currency': instrument.price_currency.code
                })
            except PriceNotFoundError as e:
                logger.warning(f"Price not found for {instrument.code}: {e}")
                errors.append({
                    'instrument': instrument.code,
                    'error': 'Price not found'
                })
            except PriceConnectorError as e:
                logger.error(f"Connector error for {instrument.code}: {e}")
                errors.append({
                    'instrument': instrument.code,
                    'error': f'Connector error: {str(e)}'
                })
        
        return {
            'total_value': str(total_value),
            'positions': positions,
            'errors': errors,
            'as_of': as_of.isoformat() if as_of else datetime.now().isoformat()
        }

# Usage
connector = ExampleHTTPConnector(api_key=settings.EXAMPLE_API_KEY)
valuation_service = PortfolioValuationService(connector)

result = valuation_service.calculate_portfolio_value(my_account)
print(f"Portfolio value: ${result['total_value']}")
for position in result['positions']:
    print(f"  {position['instrument']}: {position['quantity']} @ ${position['price']} = ${position['value']}")
```

## Related Documents

* **`README.md`** — Core package overview and features
* **`django_assets_core_extension_patterns_guide.md`** — Comprehensive guide to extending the core package

## Summary

* The core system does **not** store asset prices
* Developers build connectors to retrieve prices from external APIs
* A default reference HTTP connector connector is included
* Connectors implement a simple `PriceConnector` interface
* Connectors are used on-demand for portfolio valuation
* Each developer is responsible for building connectors appropriate for their use case
