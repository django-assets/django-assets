"""Reconciliation: the lock, the queue, and the match helpers
(brokerage spec §6; ADR-0024/0026/0028, D-17).

A leg is reconciled iff some ImportLine.matched_legs references it. The
numeric facts of a reconciled leg (amount/account/instrument) are broker
ground truth: pre_save/pre_delete handlers on core's TransactionLeg
(wired in AppConfig.ready()) raise ReconciledLegLocked on mutation or
deletion. description/metadata stay editable. Unflip
(line.matched_legs.remove) is the deliberate escape hatch; core itself
has zero reconciliation awareness — uninstalling brokerage removes the
lock, by design.
"""

from typing import Any

from django.db.models import QuerySet

from django_assets.brokerage.accounts import account_allows_reconciliation
from django_assets.brokerage.exceptions import ReconciledLegLocked
from django_assets.brokerage.models import ImportLine
from django_assets.core.models import Account, TransactionLeg

LOCKED_FIELDS = ("amount", "account_id", "instrument_id")


def guard_locked_leg_save(
    sender: type[TransactionLeg], instance: TransactionLeg, **kwargs: Any
) -> None:
    if instance.pk is None:
        return
    try:
        current = TransactionLeg.objects.get(pk=instance.pk)
    except TransactionLeg.DoesNotExist:
        return
    changed = [
        field for field in LOCKED_FIELDS if getattr(current, field) != getattr(instance, field)
    ]
    if changed and ImportLine.objects.filter(matched_legs=instance).exists():
        raise ReconciledLegLocked(
            f"leg {instance.pk} is reconciled to broker evidence; "
            f"{changed} are ground truth (D-17). Unflip the match first "
            f"(line.matched_legs.remove(leg)), then edit and re-match."
        )


def guard_locked_leg_delete(
    sender: type[TransactionLeg], instance: TransactionLeg, **kwargs: Any
) -> None:
    if instance.pk is not None and ImportLine.objects.filter(matched_legs=instance).exists():
        raise ReconciledLegLocked(
            f"leg {instance.pk} is reconciled to broker evidence and cannot "
            f"be deleted (nor can its parent Transaction). Unflip first."
        )


def unmatched_lines(account: Account | None = None) -> "QuerySet[ImportLine]":
    """The review queue: matchable lines with no matched legs."""
    queue = ImportLine.objects.filter(kind__startswith="broker_", matched_legs__isnull=True)
    if account is not None:
        queue = queue.filter(batch__account=account)
    return queue.select_related("batch").order_by("batch_id", "line_number")


def match_line(line: ImportLine, legs: list[TransactionLeg]) -> None:
    """Manual/dedup match (ADR-0024 Path 2). Eligibility enforced: every
    leg's account must pass account_allows_reconciliation (D-10)."""
    for leg in legs:
        if not account_allows_reconciliation(leg.account):
            raise ValueError(
                f"leg {leg.pk} is on {leg.account.name!r}, which has no "
                f"allows_reconciliation profile — ineligible for matching"
            )
    line.matched_legs.add(*legs)


def unmatch_line(line: ImportLine, legs: list[TransactionLeg]) -> None:
    """Unflip: the legs return to editable state, the line to the pool."""
    line.matched_legs.remove(*legs)
