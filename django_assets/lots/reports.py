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
        if match.metadata.get("return_of_capital") and match.realized_gain == 0:
            # Pure basis reductions don't belong on a 1099-B; the
            # excess-over-basis portion is a real capital gain and stays.
            continue
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
            "return_of_capital": bool(match.metadata.get("return_of_capital")),
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
    """Open positions marked via a PriceSource at its best-available
    quote (ADR-0034/0039): unpriced lots are surfaced, never zeroed."""
    from django_assets.lots.queries import open_lots

    total = Decimal(0)
    unpriced: list[str] = []
    for lot in open_lots(account, instrument):
        quote = price_source.get_quote(lot.instrument)
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


def income_summary(account: Account, year: "int | None" = None) -> "dict[str, Any]":
    """1099-DIV-shaped aggregation (ADR-0038 §4) over income-character
    metadata. `account` is the CASH account the income landed in.
    Amounts are the cash received per transaction plus any withholding
    legs booked with it (gross-up); `unclassified` renders as its own
    line — visible, never folded into ordinary."""
    from collections import defaultdict

    from django_assets.core.models import Transaction

    transactions = (
        Transaction.objects.filter(metadata__has_key="income_character", legs__account=account)
        .distinct()
        .prefetch_related("legs__account")
    )
    if year is not None:
        transactions = transactions.filter(timestamp__year=year)

    totals: dict[str, Decimal] = defaultdict(Decimal)
    labels: dict[str, set[str]] = defaultdict(set)
    for transaction in transactions:
        character = transaction.metadata["income_character"]
        cash = Decimal(0)
        withheld = Decimal(0)
        for leg in transaction.legs.all():
            if leg.account_id == account.pk:
                cash += leg.amount
            elif leg.account.name in ("tax_withheld", "foreign_tax_paid"):
                withheld += leg.amount
        totals[character] += cash + withheld
        label = transaction.metadata.get("income_label")
        if label:
            labels[character].add(label)

    ordinary = totals.get("ordinary", Decimal(0))
    qualified = totals.get("qualified", Decimal(0))
    return {
        "box_1a_total_ordinary": ordinary + qualified,
        "box_1b_qualified": qualified,
        "box_2a_capital_gain_distributions": totals.get("capital_gain_lt", Decimal(0))
        + totals.get("capital_gain_st", Decimal(0)),
        "box_3_nondividend_distributions": totals.get("return_of_capital", Decimal(0)),
        "interest": totals.get("interest", Decimal(0)),
        "exempt": totals.get("exempt", Decimal(0)),
        "unclassified": totals.get("unclassified", Decimal(0)),
        "labels": {character: sorted(seen) for character, seen in labels.items()},
    }
