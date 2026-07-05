"""Staleness marking (lots spec §2.3, ADR-0032 §4): any ledger edit
marks exactly the touched (account, instrument) pair; the next query
against it rebuilds transparently."""

from typing import Any

from django_assets.core.models import TransactionLeg
from django_assets.lots.models import StaleLotScope


def mark_stale(sender: object, instance: TransactionLeg, **kwargs: Any) -> None:
    if instance.instrument.price_currency_id is None:
        return  # cash needs no lots
    StaleLotScope.objects.get_or_create(
        account_id=instance.account_id, instrument_id=instance.instrument_id
    )
