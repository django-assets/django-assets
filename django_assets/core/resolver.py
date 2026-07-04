"""Symbol resolution (core spec §5; ADR-0009 contract, ADR-0018 shapes).

DefaultInstrumentResolver normalizes by stripping whitespace only — case
is preserved because preferred-share tickers (CDRpB, BRKpA) are genuinely
case-sensitive. Hosts with uppercase-uniform feeds swap in a subclass via
DJANGO_ASSETS_INSTRUMENT_RESOLVER. The resolver is read-only: no
resolve_or_create — instrument creation is deliberate reference-data
management, never a lookup side effect.
"""

import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils.module_loading import import_string

from django_assets.core.exceptions import (
    AmbiguousInstrumentError,
    InstrumentNotFoundError,
)

if TYPE_CHECKING:
    from django_assets.core.models import Exchange, Identifier, Instrument


class DefaultInstrumentResolver:
    """One-or-raise `resolve` and list-returning `search` (ADR-0018)."""

    def normalize(self, value: str) -> str:
        return value.strip()

    def _candidates(
        self,
        value: str,
        *,
        type: str,
        exchange: "Exchange | None",
        as_of: datetime.date | None,
    ) -> "QuerySet[Identifier]":
        from django_assets.core.models import Identifier

        qs = Identifier.objects.filter(type=type, value=self.normalize(value))
        if exchange is not None:
            # Exchange-scoped hit OR a global (NULL-exchange) identifier.
            qs = qs.filter(Q(exchange=exchange) | Q(exchange__isnull=True))
        if as_of is None:
            qs = qs.filter(is_active=True)
        else:
            # Effective-date window; NULL bounds are open-ended.
            qs = qs.filter(
                Q(effective_from__isnull=True) | Q(effective_from__lte=as_of),
                Q(effective_to__isnull=True) | Q(effective_to__gte=as_of),
            )
        return qs.select_related("instrument")

    def resolve(
        self,
        value: str,
        *,
        type: str = "ticker",
        exchange: "Exchange | None" = None,
        as_of: datetime.date | None = None,
    ) -> "Instrument":
        matches = list(self._candidates(value, type=type, exchange=exchange, as_of=as_of))
        if len(matches) == 1:
            return matches[0].instrument
        if not matches:
            raise InstrumentNotFoundError(self.normalize(value), type, exchange)
        raise AmbiguousInstrumentError(self.normalize(value), [m.instrument for m in matches])

    def search(
        self,
        value: str,
        *,
        type: str = "ticker",
        exchange: "Exchange | None" = None,
        as_of: datetime.date | None = None,
    ) -> "list[Instrument]":
        return [
            m.instrument for m in self._candidates(value, type=type, exchange=exchange, as_of=as_of)
        ]


def get_resolver() -> DefaultInstrumentResolver:
    """Instantiate the configured resolver class on each call.

    Read at call time (not import time) so override_settings and runtime
    reconfiguration behave; import_string caches the module import.
    """
    dotted = getattr(
        settings,
        "DJANGO_ASSETS_INSTRUMENT_RESOLVER",
        "django_assets.core.resolver.DefaultInstrumentResolver",
    )
    cls: type[DefaultInstrumentResolver] = import_string(dotted)
    return cls()
