"""MarketData.app PriceSource connector for django-assets.

Implements the ADR-0039 v2 price contract (capabilities, kinded quotes,
dated closes, bounded OHLCV) against MarketData.app for US equities and
options (greeks/IV). The library never imports this package; hosts
instantiate MarketDataPriceSource and pass it wherever `price_source=`
goes:

    from django_assets_prices_marketdata import MarketDataPriceSource

    source = MarketDataPriceSource()   # token from $MARKETDATA_TOKEN
    Portfolio.value(account, source)
"""

from django_assets_prices_marketdata.client import (
    MarketDataAuthError,
    MarketDataBadRequest,
    MarketDataClient,
    MarketDataEntitlementError,
    MarketDataError,
)
from django_assets_prices_marketdata.source import MarketDataPriceSource

__all__ = [
    "MarketDataAuthError",
    "MarketDataBadRequest",
    "MarketDataClient",
    "MarketDataEntitlementError",
    "MarketDataError",
    "MarketDataPriceSource",
]
