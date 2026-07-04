"""Disclosure capture (brokerage spec §8.2, ADR-0023).

Edit-in-place under the leg lock, plus a DisclosureEvent carrying a
full before-snapshot. The broker original survives in three layers:
ImportLine.raw_data (verbatim), the locked asset legs, and the first
event's snapshot (the as-materialized shape). Snapshots are storage,
not presentation — the reconstruction surfaces render them as
structured records [D-19].
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction as db_transaction

from django_assets.brokerage.exceptions import ReconciledLegLocked
from django_assets.brokerage.models import DisclosureEvent, ImportLine
from django_assets.core.exceptions import MixedOwnershipError
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg


@dataclass(frozen=True)
class LegEdit:
    """A revision to one existing (unlocked) leg; None = unchanged."""

    leg: TransactionLeg
    amount: Decimal | int | str | None = None
    account: Account | None = None
    instrument: Instrument | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class NewLeg:
    account: Account
    amount: Decimal | int | str
    instrument: Instrument | None = None
    instrument_code: str = ""
    description: str = ""
    metadata: dict[str, Any] | None = None

    def resolve_instrument(self) -> Instrument:
        if self.instrument is not None:
            return self.instrument
        return Instrument.resolve(self.instrument_code)


@dataclass(frozen=True)
class DisclosureEdits:
    revised: list[LegEdit] = field(default_factory=list)
    added: list[NewLeg] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)


def snapshot_transaction(transaction: Transaction) -> dict[str, Any]:
    """TransactionSerializer-shaped full state: transaction + ALL legs."""
    return {
        "id": transaction.pk,
        "account": transaction.account_id,
        "timestamp": transaction.timestamp.isoformat(),
        "trade_timestamp": (
            transaction.trade_timestamp.isoformat() if transaction.trade_timestamp else None
        ),
        "description": transaction.description,
        "metadata": transaction.metadata,
        "origin": transaction.origin,
        "legs": [
            {
                "id": leg.pk,
                "account": leg.account_id,
                "account_name": leg.account.name,
                "instrument": leg.instrument_id,
                "instrument_code": leg.instrument.code,
                "amount": str(leg.amount),
                "description": leg.description,
                "metadata": leg.metadata,
            }
            for leg in transaction.legs.select_related("account", "instrument").order_by("id")
        ],
    }


def _locked_leg_pks(transaction: Transaction) -> set[int]:
    return set(
        ImportLine.objects.filter(matched_legs__transaction=transaction).values_list(
            "matched_legs", flat=True
        )
    )


def apply_disclosure(
    transaction: Transaction,
    *,
    source: str,
    reference: str = "",
    note: str = "",
    effective_date: datetime.date | None = None,
    edits: DisclosureEdits,
) -> DisclosureEvent:
    """Atomically: snapshot the transaction, apply the leg edits,
    validate. Locked legs (ADR-0024) cannot appear in `edits`; the
    deferred balance trigger validates the edited transaction at COMMIT.
    Works on never-imported manual Transactions too."""
    locked = _locked_leg_pks(transaction)
    touched = [edit.leg.pk for edit in edits.revised] + list(edits.removed)
    blocked = [pk for pk in touched if pk in locked]
    if blocked:
        raise ReconciledLegLocked(
            f"legs {blocked} are reconciled to broker evidence and cannot "
            f"appear in disclosure edits (ADR-0023/0024); the broker-"
            f"reported facts are the anchor the disclosure decomposes around"
        )
    owner_id = transaction.account.owner_id

    with db_transaction.atomic():
        event = DisclosureEvent.objects.create(
            transaction=transaction,
            source=source,
            source_reference=reference,
            note=note,
            effective_date=effective_date,
            snapshot_before=snapshot_transaction(transaction),
        )
        for edit in edits.revised:
            leg = edit.leg
            if edit.amount is not None:
                leg.amount = leg.instrument.quantize(to_decimal(edit.amount), strict=True)
            if edit.account is not None:
                if edit.account.owner_id != owner_id:
                    raise MixedOwnershipError(
                        f"account {edit.account.name!r} belongs to a different owner"
                    )
                leg.account = edit.account
            if edit.instrument is not None:
                leg.instrument = edit.instrument
            if edit.description is not None:
                leg.description = edit.description
            if edit.metadata is not None:
                leg.metadata = edit.metadata
            leg.save()
        if edits.removed:
            transaction.legs.filter(pk__in=edits.removed).delete()
        for new_leg in edits.added:
            if new_leg.account.owner_id != owner_id:
                raise MixedOwnershipError(
                    f"account {new_leg.account.name!r} belongs to a different owner"
                )
            instrument = new_leg.resolve_instrument()
            TransactionLeg.objects.create(
                transaction=transaction,
                account=new_leg.account,
                instrument=instrument,
                amount=instrument.quantize(to_decimal(new_leg.amount), strict=True),
                description=new_leg.description,
                metadata=new_leg.metadata or {},
            )
    return event


def reconstruct_before(event: DisclosureEvent) -> dict[str, Any]:
    """The transaction's full state immediately before this event."""
    return dict(event.snapshot_before)


def reconstruct_original(transaction: Transaction) -> dict[str, Any]:
    """The as-imported (or as-created) state: the FIRST event's
    before-snapshot, or the live state when nothing was ever disclosed."""
    first = (
        DisclosureEvent.objects.filter(transaction=transaction)
        .order_by("disclosed_at", "id")
        .first()
    )
    if first is not None:
        return dict(first.snapshot_before)
    return snapshot_transaction(transaction)
