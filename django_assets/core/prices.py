"""PriceSource protocol + reference implementations (ADR-0034) [D-7].

Core stores no prices and ships no real providers. PriceQuote is the wire
shape; None = unpriced, surfaced honestly, never guessed. Symbol mapping
is the source's job (via Identifier). Real providers are host or sibling
implementations.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from django.utils import timezone

from django_assets.core.intake import to_decimal
from django_assets.core.models import Instrument


@dataclass(frozen=True)
class PriceQuote:
    """One observed price. `kind` is the quote flavor (last, close, static…)."""

    price: Decimal
    currency: Instrument
    as_of: datetime.datetime | None
    source: str
    kind: str

    def __post_init__(self) -> None:
        # PADR-0006 Rule 3: a float-built quote fails here, loudly.
        object.__setattr__(self, "price", to_decimal(self.price, param="price"))


@runtime_checkable
class PriceSource(Protocol):
    """Structural contract: hosts implement get_price / get_prices."""

    def get_price(
        self, instrument: Instrument, *, at: datetime.datetime | None = None
    ) -> PriceQuote | None: ...

    def get_prices(
        self, instruments: list[Instrument], *, at: datetime.datetime | None = None
    ) -> dict[Instrument, PriceQuote | None]: ...


class StaticPriceSource:
    """Fixed prices from a dict — tests, docs, and demos (ADR-0034).

    Prices are per-instrument in the instrument's own price_currency;
    Decimal/int/str only (the intake guard applies at construction).
    """

    def __init__(self, prices: dict[Instrument, Decimal | int | str]) -> None:
        self._prices = {
            inst: to_decimal(price, param=f"price[{inst.code}]") for inst, price in prices.items()
        }

    def get_price(
        self, instrument: Instrument, *, at: datetime.datetime | None = None
    ) -> PriceQuote | None:
        price = self._prices.get(instrument)
        if price is None:
            return None
        currency = instrument.price_currency
        if currency is None:
            return None
        return PriceQuote(price=price, currency=currency, as_of=at, source="static", kind="static")

    def get_prices(
        self, instruments: list[Instrument], *, at: datetime.datetime | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        return {inst: self.get_price(inst, at=at) for inst in instruments}


class CachedPriceSource:
    """TTL cache over any PriceSource; None results are cached too."""

    def __init__(self, inner: PriceSource, ttl: int) -> None:
        self.inner = inner
        self.ttl = ttl
        self._cache: dict[
            tuple[int, datetime.datetime | None],
            tuple[PriceQuote | None, datetime.datetime],
        ] = {}

    def get_price(
        self, instrument: Instrument, *, at: datetime.datetime | None = None
    ) -> PriceQuote | None:
        now = timezone.now()
        key = (instrument.pk, at)
        hit = self._cache.get(key)
        if hit is not None:
            quote, cached_at = hit
            if (now - cached_at).total_seconds() <= self.ttl:
                return quote
        quote = self.inner.get_price(instrument, at=at)
        self._cache[key] = (quote, now)
        return quote

    def get_prices(
        self, instruments: list[Instrument], *, at: datetime.datetime | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        return {inst: self.get_price(inst, at=at) for inst in instruments}
