"""Shared plumbing for the option-tracker app: the one process-wide
price source and the demo-user lookup. No domain logic — the app only
fetches library reports and renders them."""

from django.contrib.auth.models import User
from django_assets_prices_marketdata import MarketDataPriceSource

from django_assets.core.models import Account
from django_assets.core.prices import CachedPriceSource, PriceSource

_price_source: PriceSource | None = None


def price_source() -> PriceSource:
    """Process-wide singleton, built LAZILY on first use: MarketData
    quotes behind a TTL cache so a page render costs at most one metered
    fetch per instrument per 5 minutes (history bars per 4 hours). Token
    comes from
    $MARKETDATA_TOKEN — resolved when a tracker view first needs a
    price, never at import (the rest of the project must not require a
    vendor credential)."""
    global _price_source
    if _price_source is None:
        _price_source = CachedPriceSource(MarketDataPriceSource(), ttl=300, history_ttl=14400)
    return _price_source


#: The user-side accounts. "market" is the seeded counterparty account
#: (ADR-0035 naming convention) and never counts toward the user's book.
USER_ACCOUNT_NAMES = ("cash", "holdings")


def demo_user() -> User:
    return User.objects.get(username="demo")


def user_accounts(user: User) -> list[Account]:
    return list(Account.objects.filter(owner=user, name__in=USER_ACCOUNT_NAMES))
