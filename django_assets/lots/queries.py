"""Lots query surface with auto-rebuild-on-query (ADR-0032 §4):
stale numbers are never served; no manual step exists in the normal
flow."""

from django_assets.core.models import Account, Instrument
from django_assets.lots.models import Lot, StaleLotScope
from django_assets.lots.rebuild import rebuild_lots


def ensure_fresh(account: Account, instrument: Instrument | None = None) -> None:
    stale = StaleLotScope.objects.filter(account=account)
    if instrument is not None:
        stale = stale.filter(instrument=instrument)
    for scope in stale.select_related("instrument"):
        rebuild_lots(account, scope.instrument)


def open_lots(account: Account, instrument: Instrument | None = None) -> "list[Lot]":
    ensure_fresh(account, instrument)
    lots = Lot.objects.filter(account=account, quantity_remaining__gt=0)
    if instrument is not None:
        lots = lots.filter(instrument=instrument)
    return list(lots.select_related("instrument").order_by("acquired_at", "id"))
