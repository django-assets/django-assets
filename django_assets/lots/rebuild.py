"""The lots rebuild: a pure function of the ledger (+ linkage records)
— identical inputs, identical rows (lots spec §2.3, ADR-0032 §4).

Truncate-and-rewrite per (account, instrument) scope. The deferred
lot_conservation trigger validates final state at COMMIT; the
end-of-rebuild assertion is the fast-fail (and the only enforcement
when USE_DB_TRIGGERS=False).
"""

import datetime
from decimal import Decimal
from typing import Any

from django.db import models
from django.db import transaction as db_transaction

from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg
from django_assets.lots.models import Lot, LotEvent, LotMatch, StaleLotScope

ADJUSTING_ACTIONS = ("split", "reverse_split", "stock_dividend")


def rebuild_lots(
    account: Account,
    instrument: Instrument | None = None,
    from_: datetime.datetime | None = None,
) -> None:
    """Deterministic rebuild; `from_` is accepted for API stability but
    v1 always rebuilds the whole scope (correct beats clever)."""
    instruments = (
        [instrument]
        if instrument is not None
        else list(
            Instrument.objects.filter(
                transactionleg__account=account,
                price_currency__isnull=False,  # cash needs no lots
            ).distinct()
        )
    )
    with db_transaction.atomic():
        for scoped in instruments:
            Lot.objects.filter(account=account, instrument=scoped).delete()
            _build_scope(account, scoped)
        _assert_conservation(account, instruments)
        StaleLotScope.objects.filter(
            account=account, instrument__in=[inst.pk for inst in instruments]
        ).delete()


def _build_scope(account: Account, instrument: Instrument) -> None:
    legs = (
        TransactionLeg.objects.filter(account=account, instrument=instrument)
        .select_related("transaction")
        .order_by("transaction__timestamp", "transaction_id", "id")
    )
    open_lots: list[Lot] = []

    for leg in legs:
        transaction = leg.transaction
        tag = transaction.metadata.get("corporate_action")
        if tag and tag.get("instrument_id") == instrument.pk:
            if tag.get("type") in ADJUSTING_ACTIONS:
                _apply_ratio_adjustment(open_lots, transaction, tag)
            continue  # tagged transactions are interpreted, not walked
        if transaction.metadata.get("conversion") or transaction.metadata.get("rollover"):
            # Linked-carryover handling arrives with milestone L3; until
            # then these walk as plain quantity events.
            pass

        quantity = leg.amount
        cash = _attributable_cash(transaction, account, instrument)
        position = sum(lot.quantity_remaining * _sign(lot) for lot in open_lots)
        if position == 0 or (quantity > 0) == (position > 0):
            _open_lot(open_lots, account, instrument, leg, quantity, cash)
        else:
            remainder, remainder_cash = _close_against(open_lots, leg, quantity, cash)
            if remainder:
                _open_lot(open_lots, account, instrument, leg, remainder, remainder_cash)

    for lot in open_lots:
        lot.save()


def _sign(lot: Lot) -> int:
    return 1 if lot.direction == "long" else -1


def _open_lot(
    open_lots: list[Lot],
    account: Account,
    instrument: Instrument,
    leg: TransactionLeg,
    quantity: Decimal,
    cash: Decimal,
) -> None:
    lot = Lot(
        account=account,
        instrument=instrument,
        opened_by_leg=leg,
        acquired_at=leg.transaction.trade_timestamp or leg.transaction.timestamp,
        quantity=abs(quantity),
        quantity_remaining=abs(quantity),
        cost_basis=abs(cash),
        cost_basis_remaining=abs(cash),
        direction="long" if quantity > 0 else "short",
    )
    lot.save()
    open_lots.append(lot)


def _close_against(
    open_lots: list[Lot],
    closing_leg: TransactionLeg,
    quantity: Decimal,
    cash: Decimal,
) -> tuple[Decimal, Decimal]:
    """Consume FIFO lots; returns any zero-crossing remainder and its
    cash share."""
    to_close = abs(quantity)
    total = to_close
    for lot in list(open_lots):
        if to_close == 0:
            break
        if lot.quantity_remaining == 0:
            continue
        consumed = min(lot.quantity_remaining, to_close)
        share = consumed / lot.quantity
        basis_recovered = lot.cost_basis * share
        proceeds = abs(cash) * consumed / total if total else Decimal(0)
        gain = proceeds - basis_recovered if lot.direction == "long" else basis_recovered - proceeds
        lot.quantity_remaining -= consumed
        lot.cost_basis_remaining -= basis_recovered
        lot.save()
        LotMatch.objects.create(
            lot=lot,
            closing_leg=closing_leg,
            quantity=consumed,
            proceeds=proceeds,
            basis_recovered=basis_recovered,
            realized_gain=gain,
            term=_term(lot.acquired_at, closing_leg.transaction.timestamp),
        )
        to_close -= consumed
    remainder_sign = 1 if quantity > 0 else -1
    remainder = to_close * remainder_sign
    remainder_cash = abs(cash) * to_close / total if total else Decimal(0)
    return remainder, remainder_cash


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
    open_lots: list[Lot], transaction: Transaction, tag: dict[str, Any]
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


def _attributable_cash(
    transaction: Transaction, account: Account, instrument: Instrument
) -> Decimal:
    """The user-side net cash of the event (US convention: fees already
    inside the net perspective-cash leg, so basis capitalizes and
    proceeds net out with no double counting).

    Role heuristic (the trades twin, duplicated here because lots may
    not import trades — ADR-0015 DAG): the mirror account is the one
    holding the opposite-sign leg of the scoped instrument; the user
    cash slice is the largest cash-role leg not on it.
    """
    legs = list(transaction.legs.select_related("instrument", "account"))
    scoped = [leg for leg in legs if leg.instrument_id == instrument.pk]
    mirror_accounts = {leg.account_id for leg in scoped if leg.account_id != account.pk}
    cash_legs = [
        leg
        for leg in legs
        if leg.instrument.price_currency_id is None and leg.account_id not in mirror_accounts
    ]
    if not cash_legs:
        return Decimal(0)
    user_cash = max(cash_legs, key=lambda leg: abs(leg.amount))
    return user_cash.amount


def _assert_conservation(account: Account, instruments: "list[Instrument]") -> None:
    """Fast-fail mirror of the lot_conservation trigger."""
    lots = Lot.objects.filter(account=account, instrument__in=[i.pk for i in instruments])
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
            f"lot conservation violated for lot {lot.pk}: "
            f"remaining {lot.quantity_remaining} vs {lot.quantity} − {matched}"
        )
