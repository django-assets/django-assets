"""Virtual-transfer helpers (trades spec §5, ADR-0031)."""

import datetime
from decimal import Decimal
from typing import Any, cast

from django.db import transaction as db_transaction

from django_assets.core.intake import to_decimal
from django_assets.core.models import Instrument
from django_assets.trades.exceptions import UnbalancedVirtualTransferError
from django_assets.trades.models import (
    PositionCrossingWarning,
    Trade,
    VirtualEntry,
    VirtualTransfer,
)


def record_virtual_transfer(
    user: object,
    timestamp: datetime.datetime,
    *,
    entries: "list[dict[str, Any]]",
    description: str = "",
) -> VirtualTransfer:
    """Arbitrary balanced entry sets (one source feeding two destinations,
    …). Hard rules: per-instrument zero sums, same-user trades. Crossing
    only WARNS (attached to the result), never blocks."""
    prepared = [
        {
            "trade": entry["trade"],
            "instrument": entry["instrument"],
            "amount": entry["instrument"].quantize(to_decimal(entry["amount"]), strict=True),
            "category": entry.get("category", ""),
            "metadata": entry.get("metadata", {}),
        }
        for entry in entries
    ]
    for entry in prepared:
        if entry["trade"].user_id != getattr(user, "pk", user):
            raise ValueError(
                f"trade {entry['trade'].name!r} belongs to a different user; "
                f"virtual transfers never cross user boundaries (ADR-0031)"
            )
    sums: dict[int, Decimal] = {}
    codes: dict[int, str] = {}
    for entry in prepared:
        inst = entry["instrument"]
        sums[inst.pk] = sums.get(inst.pk, Decimal(0)) + entry["amount"]
        codes[inst.pk] = inst.code
    off = {codes[pk]: str(total) for pk, total in sums.items() if total != 0}
    if off:
        raise UnbalancedVirtualTransferError(
            f"virtual entries are not balanced per instrument: {off}"
        )

    warnings = [
        warning
        for entry in prepared
        if entry["category"] == ""
        for warning in _crossing_check(
            entry["trade"], entry["instrument"], entry["amount"], timestamp
        )
    ]
    with db_transaction.atomic():
        transfer = VirtualTransfer.objects.create(
            # cast: host-generic user vs the stubs' concrete User model.
            user=cast(Any, user),
            timestamp=timestamp,
            description=description,
        )
        VirtualEntry.objects.bulk_create(
            VirtualEntry(transfer=transfer, **entry) for entry in prepared
        )
    transfer.warnings = warnings
    return transfer


def transfer_position(
    from_trade: Trade,
    to_trade: Trade,
    *,
    instrument: Instrument,
    quantity: Decimal | int | str,
    price: Decimal | int | str,
    cash_instrument: Instrument | None = None,
    timestamp: datetime.datetime,
    description: str = "",
) -> VirtualTransfer:
    """The standard four-entry balanced transfer: sign orientation
    follows the position being moved (closing a short mirrors)."""
    qty = to_decimal(quantity, param="quantity")
    unit_price = to_decimal(price, param="price")
    currency = cash_instrument or instrument.price_currency
    if currency is None:
        raise ValueError(f"{instrument.code} has no price_currency; pass cash_instrument=")
    cash = currency.quantize(qty * unit_price * instrument.multiplier, strict=True)
    direction = 1 if from_trade.net_position(instrument) >= 0 else -1
    return record_virtual_transfer(
        from_trade.user,
        timestamp,
        description=description or f"transfer {quantity} {instrument.code}",
        entries=[
            {"trade": from_trade, "instrument": instrument, "amount": -direction * qty},
            {
                "trade": from_trade,
                "instrument": currency,
                "amount": direction * cash,
                "category": "revenue" if direction > 0 else "cost",
            },
            {"trade": to_trade, "instrument": instrument, "amount": direction * qty},
            {
                "trade": to_trade,
                "instrument": currency,
                "amount": -direction * cash,
                "category": "cost" if direction > 0 else "revenue",
            },
        ],
    )


def _crossing_check(
    trade: Trade,
    instrument: Instrument,
    amount: Decimal,
    timestamp: datetime.datetime,
) -> "list[PositionCrossingWarning]":
    """Warn when the entry pushes the as-of book THROUGH zero (touch-zero
    and open-from-zero are fine) — advisory only, never blocking."""
    positions: dict[int, Decimal] = {}
    for ts, delta, instrument_id in trade._position_events([instrument]):
        if ts > timestamp:
            break
        positions[instrument_id] = positions.get(instrument_id, Decimal(0)) + delta
    before = positions.get(instrument.pk, Decimal(0))
    if before != 0 and (amount > 0) != (before > 0) and abs(amount) > abs(before):
        return [
            PositionCrossingWarning(
                trade,
                instrument,
                "position",
                f"entry {amount} pushes {trade.name!r} from {before} through zero "
                f"in {instrument.code}; the excess lands in the counterparty "
                f"trade(s) (ADR-0031)",
            )
        ]
    return []
