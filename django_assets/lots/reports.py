"""Report views (lots spec §2.4/L5 slice needed by L3 tests): stored
facts in their real currencies; rates only at view time."""

from decimal import Decimal
from typing import Any

from django_assets.core.models import Account, Instrument
from django_assets.lots.models import LotMatch
from django_assets.lots.queries import ensure_fresh


def realized_gains(
    account: Account,
    year: int | None = None,
    *,
    fx: Any | None = None,
    currency: Instrument | None = None,
) -> "list[dict[str, Any]]":
    """1099-B-shaped rows. Cross-currency matches render as honest
    currency pairs with a derived implied rate; with fx= and currency=,
    the basis converts under the supplied source at view time — storage
    is never touched."""
    ensure_fresh(account)
    matches = LotMatch.objects.filter(lot__account=account).select_related(
        "lot", "lot__instrument", "closing_leg__transaction"
    )
    if year is not None:
        matches = matches.filter(closing_leg__transaction__timestamp__year=year)
    rows: list[dict[str, Any]] = []
    for match in matches.order_by("closing_leg__transaction__timestamp", "id"):
        row: dict[str, Any] = {
            "instrument": match.lot.instrument.code,
            "quantity": match.quantity,
            "acquired_at": match.lot.acquired_at,
            "closed_at": match.closing_leg.transaction.timestamp,
            "proceeds": match.proceeds,
            "basis": match.basis_recovered,
            "realized_gain": match.realized_gain,
            "term": match.term,
            "unlinked": bool(match.lot.metadata.get("unlinked")),
            "wash_sale_disallowed": sum(
                (adj.disallowed_loss for adj in match.wash_sale_adjustments.all()),
                Decimal(0),
            ),
        }
        if match.metadata.get("cross_currency"):
            basis_currency = match.metadata["basis_currency"]
            proceeds_currency = match.metadata["proceeds_currency"]
            row["cross_currency"] = True
            row["basis_currency"] = basis_currency
            row["proceeds_currency"] = proceeds_currency
            rate = None
            if fx is not None and currency is not None:
                on = match.closing_leg.transaction.timestamp.date()
                rate = fx.get_rate(basis_currency, currency.code, on)
            if rate is not None:
                converted_basis = match.basis_recovered / rate
                row["basis_converted"] = converted_basis
                row["realized_gain"] = match.proceeds - converted_basis
            else:
                # The implicit-conversion view: the operation's own rate,
                # derived at view time, existing nowhere in storage.
                row["implied_rate"] = (
                    match.basis_recovered / match.proceeds if match.proceeds else None
                )
                row["realized_gain"] = None  # honest pair, no single number
        rows.append(row)
    return rows


def unrealized(
    account: Account,
    price_source: Any,
    instrument: Instrument | None = None,
) -> "dict[str, Any]":
    """Open positions marked via a PriceSource (ADR-0034): unpriced
    lots are surfaced, never zeroed."""
    from django_assets.lots.queries import open_lots

    total = Decimal(0)
    unpriced: list[str] = []
    for lot in open_lots(account, instrument):
        quote = price_source.get_price(lot.instrument)
        if quote is None:
            unpriced.append(lot.instrument.code)
            continue
        sign = 1 if lot.direction == "long" else -1
        market = quote.price * lot.quantity_remaining * lot.instrument.multiplier
        total += sign * (market - lot.cost_basis_remaining)
    return {"unrealized_gain": total, "unpriced": unpriced}


def open_lots_report(
    account: Account, instrument: Instrument | None = None
) -> "list[dict[str, Any]]":
    from django_assets.lots.queries import open_lots

    return [
        {
            "instrument": lot.instrument.code,
            "acquired_at": lot.acquired_at,
            "quantity_remaining": lot.quantity_remaining,
            "cost_basis_remaining": lot.cost_basis_remaining,
            "direction": lot.direction,
            "rollover_linked": lot.rollover_linked,
            "unlinked": bool(lot.metadata.get("unlinked")),
            "basis_currency": lot.metadata.get("basis_currency"),
        }
        for lot in open_lots(account, instrument)
    ]
