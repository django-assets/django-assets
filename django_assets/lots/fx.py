"""FxRateSource protocol (ADR-0032 §5): rates are a REPORTING-VIEW
parameter, never storage. The package ships only this interface (and
test stubs); real sources are host or sibling implementations — the
django-assets-fx-rates slot from ADR-0015."""

import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from django_assets.core.models import Instrument


@runtime_checkable
class FxRateSource(Protocol):
    def get_rate(
        self, base: Instrument | str, quote: Instrument | str, on: datetime.date
    ) -> Decimal | None:
        """Units of `base` per ONE unit of `quote` on the given date
        (e.g. get_rate('ARS', 'USD', d) → Decimal('1000')). None means
        the pair is unavailable; rows then render as honest currency
        pairs with the operation's own implied rate."""
        ...
