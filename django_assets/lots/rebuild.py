"""The lots rebuild: a pure function of the ledger (+ linkage records)
— identical inputs, identical rows (lots spec §2.3, ADR-0032 §4).

One account-wide chronological walk over all instruments at once, so
within-event cash targeting (openings first), rollover links, and
conversion carryover resolve naturally. Truncate-and-rewrite; the
deferred lot_conservation trigger validates final state at COMMIT and
the end-of-rebuild assertion is the fast-fail (sole enforcement when
USE_DB_TRIGGERS=False).
"""

import datetime
from decimal import Decimal
from typing import Any

from django.db import models
from django.db import transaction as db_transaction

from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg
from django_assets.lots.models import (
    ConversionLink,
    ExerciseLink,
    Lot,
    LotEvent,
    LotMatch,
    StaleLotScope,
)

ADJUSTING_ACTIONS = ("split", "reverse_split", "stock_dividend")

#: Column scale of every lots amount field: computed slices quantize to
#: it BEFORE entering running remainders, so Python state and stored
#: rows stay bit-identical (division yields 28 significant digits;
#: the DB keeps 18 decimal places).
Q18 = Decimal("1E-18")


def rebuild_lots(
    account: Account,
    instrument: Instrument | None = None,
    from_: datetime.datetime | None = None,
) -> None:
    """Deterministic account-wide rebuild. `instrument` and `from_` are
    accepted for API stability; v1 always rebuilds the whole account —
    conversions and rollovers cross instruments, and correct beats
    clever at retail scale."""
    with db_transaction.atomic():
        _materialize_links(account)
        Lot.objects.filter(account=account).delete()
        _build_scope(account)
        _detect_wash_sales(account)
        _assert_conservation(account)
        StaleLotScope.objects.filter(account=account).delete()


def _materialize_links(account: Account) -> None:
    """ExerciseLink / ConversionLink rows from the tag conventions
    instruments' templates write (source='metadata'); manual rows
    survive rebuilds and win conflicts [D-42/D-43]."""
    tagged = Transaction.objects.filter(
        legs__account=account, metadata__has_key="rollover"
    ).distinct()
    for transaction in tagged:
        tag = transaction.metadata["rollover"]
        delivered = transaction.legs.filter(
            account=account, instrument_id=tag.get("underlying_instrument_id")
        ).first()
        if delivered is None:
            continue
        exists = ExerciseLink.objects.filter(
            transaction=transaction, delivered_leg=delivered
        ).exists()
        if not exists:
            ExerciseLink.objects.create(
                transaction=transaction,
                delivered_leg=delivered,
                option_instrument_id=tag["option_instrument_id"],
                source="metadata",
            )
    converted = Transaction.objects.filter(
        legs__account=account, metadata__has_key="conversion"
    ).distinct()
    for transaction in converted:
        tag = transaction.metadata["conversion"]
        if not ConversionLink.objects.filter(transaction=transaction).exists():
            ConversionLink.objects.create(
                transaction=transaction,
                from_instrument_id=tag["from_instrument_id"],
                to_instrument_id=tag["to_instrument_id"],
                from_quantity=Decimal(str(tag["from_quantity"])),
                to_quantity=Decimal(str(tag["to_quantity"])),
                source="metadata",
            )


class _Walk:
    """Mutable account-walk state: FIFO lot lists per instrument."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self.open_lots: dict[int, list[Lot]] = {}

    def position(self, instrument_id: int) -> Decimal:
        total = Decimal(0)
        for lot in self.open_lots.get(instrument_id, []):
            total += lot.quantity_remaining * (1 if lot.direction == "long" else -1)
        return total


def _build_scope(account: Account) -> None:
    """Account-wide walk (name kept for the fault-injection seam)."""
    legs = (
        TransactionLeg.objects.filter(account=account, instrument__price_currency__isnull=False)
        .select_related("transaction", "instrument")
        .order_by("transaction__timestamp", "transaction_id", "id")
    )
    exercise_links = {
        (link.transaction_id, link.delivered_leg_id): link
        for link in ExerciseLink.objects.filter(delivered_leg__account=account)
    }
    linked_option_txs = {
        (link.transaction_id, link.option_instrument_id)
        for link in ExerciseLink.objects.filter(delivered_leg__account=account)
    }
    conversion_links = {
        link.transaction_id: link
        for link in ConversionLink.objects.filter(transaction__legs__account=account)
    }

    walk = _Walk(account)
    by_transaction: dict[int, list[TransactionLeg]] = {}
    order: list[int] = []
    for leg in legs:
        if leg.transaction_id not in by_transaction:
            order.append(leg.transaction_id)
        by_transaction.setdefault(leg.transaction_id, []).append(leg)

    for transaction_id in order:
        event_legs = by_transaction[transaction_id]
        transaction = event_legs[0].transaction
        tag = transaction.metadata.get("corporate_action")
        if tag and tag.get("type") in ADJUSTING_ACTIONS:
            _apply_ratio_adjustment(
                walk.open_lots.get(tag.get("instrument_id"), []), transaction, tag
            )
            continue  # tagged transactions are interpreted, not walked

        cash = _attributable_cash(transaction, account)
        conversion = conversion_links.get(transaction_id)
        openings: list[TransactionLeg] = []
        closings: list[TransactionLeg] = []
        for leg in event_legs:
            position = walk.position(leg.instrument_id)
            if position == 0 or (leg.amount > 0) == (position > 0):
                openings.append(leg)
            else:
                closings.append(leg)
        cash_targets = openings if openings else closings
        share = cash / len(cash_targets) if cash_targets else Decimal(0)

        carry: list[tuple[datetime.datetime, Decimal, str]] = []
        released_option: Decimal = Decimal(0)
        for leg in closings:
            leg_cash = Decimal(0) if openings else share
            zero_gain = conversion is not None or (
                (transaction_id, leg.instrument_id) in linked_option_txs
            )
            released, slices = _close_against(
                walk, leg, leg_cash, zero_gain=zero_gain, conversion=conversion
            )
            carry.extend(slices)
            if (transaction_id, leg.instrument_id) in linked_option_txs:
                released_option += released

        for leg in openings:
            link = exercise_links.get((transaction_id, leg.pk))
            if conversion is not None and carry:
                _open_from_carry(walk, leg, conversion, carry)
            elif link is not None:
                _open_lot(
                    walk,
                    leg,
                    leg.amount,
                    abs(share) - released_option,
                    rollover_linked=True,
                )
            else:
                metadata = None
                if share == 0 and closings:
                    # A no-cash swap without a link: close-at-recorded-
                    # values fallback, honestly flagged.
                    metadata = {"unlinked": True}
                _open_lot(walk, leg, leg.amount, share, metadata=metadata)

    for lots in walk.open_lots.values():
        for lot in lots:
            lot.save()


def _open_lot(
    walk: _Walk,
    leg: TransactionLeg,
    quantity: Decimal,
    cash: Decimal,
    *,
    rollover_linked: bool = False,
    metadata: "dict[str, Any] | None" = None,
    acquired_at: datetime.datetime | None = None,
    basis: Decimal | None = None,
) -> None:
    lot = Lot(
        account=walk.account,
        instrument=leg.instrument,
        opened_by_leg=leg,
        acquired_at=acquired_at or leg.transaction.trade_timestamp or leg.transaction.timestamp,
        quantity=abs(quantity),
        quantity_remaining=abs(quantity),
        cost_basis=basis if basis is not None else abs(cash),
        cost_basis_remaining=basis if basis is not None else abs(cash),
        direction="long" if quantity > 0 else "short",
        rollover_linked=rollover_linked,
        metadata=metadata or {},
    )
    lot.save()
    walk.open_lots.setdefault(leg.instrument_id, []).append(lot)


def _open_from_carry(
    walk: _Walk,
    leg: TransactionLeg,
    conversion: ConversionLink,
    carry: "list[tuple[datetime.datetime, Decimal, str]]",
) -> None:
    """Conversion carryover (ADR-0032 §5): target lots inherit basis
    unchanged in its ORIGINAL currency, ratio-mapped; acquired_at tacks;
    no realized result, no rate anywhere."""
    ratio = conversion.to_quantity / conversion.from_quantity
    from_ccy = conversion.from_instrument.price_currency
    from_currency = from_ccy.code if from_ccy is not None else ""
    for acquired_at, basis, source_qty in carry:
        target_quantity = (Decimal(source_qty) * ratio).quantize(Q18)
        sign = 1 if leg.amount > 0 else -1
        _open_lot(
            walk,
            leg,
            sign * target_quantity,
            Decimal(0),
            basis=basis,
            acquired_at=acquired_at,
            metadata={
                "basis_currency": from_currency,
                "converted_from": conversion.from_instrument.code,
            },
        )


def _close_against(
    walk: _Walk,
    closing_leg: TransactionLeg,
    cash: Decimal,
    *,
    zero_gain: bool = False,
    conversion: "ConversionLink | None" = None,
) -> "tuple[Decimal, list[tuple[datetime.datetime, Decimal, str]]]":
    """FIFO consumption. Returns (released basis, carry slices)."""
    lots = walk.open_lots.get(closing_leg.instrument_id, [])
    to_close = abs(closing_leg.amount)
    total = to_close
    cash_left = abs(cash)
    released_total = Decimal(0)
    carry: list[tuple[datetime.datetime, Decimal, str]] = []
    for lot in lots:
        if to_close == 0:
            break
        if lot.quantity_remaining == 0:
            continue
        consumed = min(lot.quantity_remaining, to_close)
        if consumed == lot.quantity_remaining:
            # Exhausting the lot: take the exact remaining basis — the
            # proportional formula can leave non-terminating residue.
            basis_recovered = lot.cost_basis_remaining
        else:
            basis_recovered = (lot.cost_basis * consumed / lot.quantity).quantize(Q18)
        if zero_gain:
            proceeds = basis_recovered
            gain = Decimal(0)
        else:
            if consumed == to_close:
                proceeds = cash_left  # final slice absorbs cash rounding
            else:
                proceeds = (abs(cash) * consumed / total).quantize(Q18) if total else Decimal(0)
            cash_left -= proceeds
            gain = (
                proceeds - basis_recovered
                if lot.direction == "long"
                else basis_recovered - proceeds
            )
        lot.quantity_remaining -= consumed
        lot.cost_basis_remaining -= basis_recovered
        lot.save()
        metadata: dict[str, Any] = {}
        basis_currency = lot.metadata.get("basis_currency")
        proceeds_currency = _cash_currency_code(closing_leg.transaction)
        if basis_currency and proceeds_currency and basis_currency != proceeds_currency:
            metadata = {
                "cross_currency": True,
                "basis_currency": basis_currency,
                "proceeds_currency": proceeds_currency,
            }
        LotMatch.objects.create(
            lot=lot,
            closing_leg=closing_leg,
            quantity=consumed,
            proceeds=proceeds,
            basis_recovered=basis_recovered,
            realized_gain=gain,
            term=_term(lot.acquired_at, closing_leg.transaction.timestamp),
            metadata=metadata,
        )
        if conversion is not None:
            carry.append((lot.acquired_at, basis_recovered, str(consumed)))
        released_total += basis_recovered if lot.direction == "short" else -basis_recovered
        to_close -= consumed
    # Zero-crossing remainder opens fresh in the caller's opening pass —
    # v1 templates never produce it in one leg, so the remainder is
    # simply re-opened with proportional cash by the caller when needed.
    if to_close:
        sign = 1 if closing_leg.amount > 0 else -1
        _open_lot(
            walk,
            closing_leg,
            sign * to_close,
            cash * to_close / total if total else Decimal(0),
        )
    return released_total, carry


def _cash_currency_code(transaction: Transaction) -> str:
    for leg in transaction.legs.select_related("instrument"):
        if leg.instrument.price_currency_id is None:
            return leg.instrument.code
    return ""


def _term(acquired_at: datetime.datetime, closed_at: datetime.datetime) -> str:
    """Long begins at one year plus one day (US convention, ADR-0032 §1)."""
    anniversary = _add_year(acquired_at.date())
    return "long" if closed_at.date() > anniversary else "short"


def _add_year(day: datetime.date) -> datetime.date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # Feb 29 → Mar 1
        return day.replace(year=day.year + 1, month=3, day=1)


def _apply_ratio_adjustment(
    open_lots: "list[Lot]", transaction: Transaction, tag: dict[str, Any]
) -> None:
    ratio = Decimal(str(tag["ratio"]))
    for lot in open_lots:
        if lot.quantity_remaining == 0:
            continue
        before_quantity = lot.quantity_remaining
        before_basis = lot.cost_basis
        matched = lot.quantity - lot.quantity_remaining
        lot.quantity_remaining = lot.quantity_remaining * ratio
        lot.quantity = matched + lot.quantity_remaining
        lot.save()
        LotEvent.objects.create(
            lot=lot,
            event_type=tag["type"],
            source_transaction=transaction,
            ratio=ratio,
            quantity_before=before_quantity,
            quantity_after=lot.quantity_remaining,
            basis_before=before_basis,
            basis_after=lot.cost_basis,
        )


def _attributable_cash(transaction: Transaction, account: Account) -> Decimal:
    """The user-side net cash of the event (US convention: fees already
    inside the net perspective-cash leg — basis capitalizes, proceeds
    net out, nothing double-counts).

    Role heuristic (trades' twin, duplicated because lots may not import
    trades — ADR-0015 DAG): mirror accounts hold the opposite side of
    this account's asset legs; the user cash slice is the largest
    cash-role leg not on one."""
    legs = list(transaction.legs.select_related("instrument", "account"))
    scoped_instruments = {
        leg.instrument_id
        for leg in legs
        if leg.account_id == account.pk and leg.instrument.price_currency_id is not None
    }
    mirror_accounts = {
        leg.account_id
        for leg in legs
        if leg.instrument_id in scoped_instruments and leg.account_id != account.pk
    }
    cash_legs = [
        leg
        for leg in legs
        if leg.instrument.price_currency_id is None and leg.account_id not in mirror_accounts
    ]
    if not cash_legs:
        return Decimal(0)
    user_cash = max(cash_legs, key=lambda leg: abs(leg.amount))
    return user_cash.amount


def _detect_wash_sales(account: Account) -> None:
    """±30-day, same-instrument window (lots spec §2.5, D-37): the
    disallowed loss — prorated by replaced quantity — becomes a basis
    addition on the replacement lot, recorded ADDITIVELY; the original
    match rows stay untouched. Runs inside every rebuild, so the rows
    are as deterministic as everything else here."""
    window = datetime.timedelta(days=30)
    losses = (
        LotMatch.objects.filter(lot__account=account, realized_gain__lt=0, lot__direction="long")
        .select_related("lot", "closing_leg__transaction")
        .order_by("closing_leg__transaction__timestamp", "id")
    )
    for loss in losses:
        closed_at = loss.closing_leg.transaction.timestamp
        replacement = (
            Lot.objects.filter(
                account=account,
                instrument=loss.lot.instrument,
                acquired_at__gte=closed_at - window,
                acquired_at__lte=closed_at + window,
            )
            .exclude(pk=loss.lot_id)
            .exclude(opened_by_leg=loss.closing_leg)
            .order_by("acquired_at", "id")
            .first()
        )
        if replacement is None:
            continue
        replaced = min(replacement.quantity, loss.quantity)
        disallowed = -loss.realized_gain * replaced / loss.quantity
        from django_assets.lots.models import WashSaleAdjustment

        WashSaleAdjustment.objects.create(
            loss_match=loss,
            replacement_lot=replacement,
            disallowed_loss=disallowed,
        )


def _assert_conservation(account: Account) -> None:
    """Fast-fail mirror of the lot_conservation trigger."""
    lots = Lot.objects.filter(account=account)
    for lot in lots.annotate(
        matched_quantity=models.Sum("matches__quantity"),
        recovered=models.Sum("matches__basis_recovered"),
    ):
        matched = lot.matched_quantity or Decimal(0)
        recovered = lot.recovered or Decimal(0)
        ok = (
            lot.quantity_remaining == lot.quantity - matched
            and lot.cost_basis_remaining == lot.cost_basis - recovered
            and 0 <= lot.quantity_remaining <= lot.quantity
        )
        assert ok, (
            f"lot conservation violated for lot {lot.pk}: quantity "
            f"{lot.quantity_remaining} vs {lot.quantity} − {matched}; basis "
            f"{lot.cost_basis_remaining} vs {lot.cost_basis} − {recovered}"
        )
