"""Shared plumbing for the option-tracker app: the one process-wide
price source and the demo-user lookup. No domain logic — the app only
fetches library reports and renders them."""

from django.contrib.auth.models import User
from django_assets_prices_marketdata import MarketDataPriceSource

from django_assets.core.models import Account
from django_assets.core.prices import CachedPriceSource, OptionChainSource, PriceSource

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


def chain_source() -> OptionChainSource:
    """The option-chain reader (ADR-0041) for roll candidates: the same
    MarketData singleton behind price_source(). The TTL wrapper only
    caches PriceSource reads, so chain enumeration goes to the inner
    source — call it on user action (a metered fetch), never on page
    load."""
    source = price_source()
    inner = getattr(source, "inner", source)
    if not isinstance(inner, OptionChainSource):  # pragma: no cover — MarketData implements it
        raise TypeError("price source cannot enumerate option chains")
    return inner


#: The user-side accounts. "market" is the seeded counterparty account
#: (ADR-0035 naming convention) and never counts toward the user's book.
USER_ACCOUNT_NAMES = ("cash", "holdings")


def demo_user() -> User:
    return User.objects.get(username="demo")


def user_accounts(user: User) -> list[Account]:
    return list(Account.objects.filter(owner=user, name__in=USER_ACCOUNT_NAMES))


def warm_caches() -> None:
    """Prime the price-source and report caches for the demo book so the
    first page hits render warm (the cold cost is live vendor round-trips
    for quotes, candle history, and per-instrument bound discovery).
    Presentation-side plumbing only: it just calls the library."""
    import datetime

    from django_assets.trades import reports

    try:
        user = demo_user()
        accounts = user_accounts(user)
        source = price_source()
        reports.open_option_strategies(user, source)
        reports.account_summary(user, source, accounts=accounts)
        reports.wheel_campaigns(user, source)
        reports.equity_holdings(user, source, accounts=accounts)
        today = datetime.date.today()
        reports.account_value_series(
            user, source, accounts=accounts, start=today - datetime.timedelta(days=180), end=today
        )
        reports.closed_option_strategies(user)
    except Exception:  # noqa: BLE001 — warming is best-effort; pages still work cold
        pass


def warm_caches_async() -> None:
    import threading

    threading.Thread(target=warm_caches, name="optiontracker-warm", daemon=True).start()
