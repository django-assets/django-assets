"""Manual linkage API (ADR-0032 §3/§5, D-42/D-43): for imported or
historical data lacking the tags. Manual links survive rebuilds and win
conflicts with tag-derived rows; linking marks the scope stale so the
next query rebuilds with rolled basis."""

from django_assets.core.models import Instrument, Transaction, TransactionLeg
from django_assets.lots.models import ConversionLink, ExerciseLink, StaleLotScope


def _mark_stale(leg: TransactionLeg) -> None:
    StaleLotScope.objects.get_or_create(account_id=leg.account_id, instrument_id=leg.instrument_id)


def link_exercise(
    transaction: Transaction,
    delivered_leg: TransactionLeg,
    *,
    option_instrument: Instrument,
) -> ExerciseLink:
    ExerciseLink.objects.filter(
        transaction=transaction, delivered_leg=delivered_leg
    ).delete()  # manual wins conflicts [D-42]
    link = ExerciseLink.objects.create(
        transaction=transaction,
        delivered_leg=delivered_leg,
        option_instrument=option_instrument,
        source="manual",
    )
    _mark_stale(delivered_leg)
    return link


def unlink_exercise(transaction: Transaction, delivered_leg: TransactionLeg) -> None:
    ExerciseLink.objects.filter(transaction=transaction, delivered_leg=delivered_leg).delete()
    _mark_stale(delivered_leg)


def link_conversion(
    transaction: Transaction,
    *,
    from_instrument: Instrument,
    to_instrument: Instrument,
    from_quantity: object,
    to_quantity: object,
) -> ConversionLink:
    from decimal import Decimal

    ConversionLink.objects.filter(transaction=transaction).delete()
    link = ConversionLink.objects.create(
        transaction=transaction,
        from_instrument=from_instrument,
        to_instrument=to_instrument,
        from_quantity=Decimal(str(from_quantity)),
        to_quantity=Decimal(str(to_quantity)),
        source="manual",
    )
    for leg in transaction.legs.all():
        _mark_stale(leg)
    return link


def unlink_conversion(transaction: Transaction) -> None:
    ConversionLink.objects.filter(transaction=transaction).delete()
    for leg in transaction.legs.all():
        _mark_stale(leg)
