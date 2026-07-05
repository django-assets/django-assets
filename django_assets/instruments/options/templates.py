"""Option lifecycle templates (instruments spec §3.4; contract §4).

buy/sell reuse the equity trade shape with the contract multiplier in
the premium. exercise/assign are deliverable-driven: the basket in force
at trade_timestamp (falling back to timestamp — ADR-0012, the PFE1
cutover semantics) generates the legs, and both write the ADR-0032 §3
rollover tag that lots materializes ExerciseLink from.
"""

import datetime
from decimal import Decimal
from typing import Any

from django_assets.core.builder import TransactionBuilder
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.instruments import tags
from django_assets.instruments.base import cash_currency, routed
from django_assets.instruments.base import share_trade as _share_trade
from django_assets.instruments.options.models import OptionMeta

Amount = Decimal | int | str
AccountMap = dict[str, Account]


def buy_option(*, contracts: Amount, **kwargs: Any) -> Transaction:
    """Premium = contracts × price × multiplier (HIMS T2 shape)."""
    kwargs.setdefault("description", f"buy {contracts} {kwargs['instrument'].code}")
    return _share_trade(side=+1, quantity=contracts, **kwargs)


def sell_option(*, contracts: Amount, **kwargs: Any) -> Transaction:
    """The ADR-0020 HIMS T1 golden shape."""
    kwargs.setdefault("description", f"sell {contracts} {kwargs['instrument'].code}")
    return _share_trade(side=-1, quantity=contracts, **kwargs)


def _option_meta(instrument: Instrument) -> OptionMeta:
    return OptionMeta.objects.select_related("underlying").get(instrument=instrument)


def _deliverable_rows(
    meta: OptionMeta,
    on: datetime.date,
    override: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if override is not None:
        return override
    rows: list[dict[str, Any]] = []
    for deliverable in meta.active_deliverables(on):
        if deliverable.instrument_id is not None:
            rows.append({"instrument": deliverable.instrument, "quantity": deliverable.quantity})
        else:
            rows.append(
                {
                    "cash_currency": deliverable.cash_currency,
                    "cash_amount": deliverable.cash_amount,
                }
            )
    if not rows:
        raise ValueError(
            f"no deliverable rows in force for {meta.instrument.code} on {on}; "
            f"populate Deliverable history or pass override_deliverables="
        )
    return rows


def _settle_option(
    *,
    kind: str,  # "exercise" | "assignment"
    accounts: AccountMap,
    instrument: Instrument,
    contracts: Amount,
    option_delta: Decimal,  # holdings change in contracts (closes the position)
    basket_side: int,  # +1 deliverables IN to holdings, -1 OUT
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None,
    override_deliverables: list[dict[str, Any]] | None,
    currency: Instrument | None,
    origin: str,
    meta: OptionMeta,
) -> Transaction:
    qty = to_decimal(contracts, param="contracts")
    ccy = cash_currency(instrument, currency)
    # ADR-0012: deliverable lookup keys on execution time, not settlement.
    lookup_at = trade_timestamp if trade_timestamp is not None else timestamp
    basket = _deliverable_rows(meta, lookup_at.date(), override_deliverables)
    strike_cash = ccy.quantize(meta.strike * instrument.multiplier * qty, strict=True)

    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"{kind} {contracts} {instrument.code}",
        origin=origin,
        metadata={
            "rollover": tags.rollover_tag(
                kind,
                option_instrument=instrument,
                underlying_instrument=meta.underlying,
                contracts=qty,
                strike=meta.strike,
                multiplier=instrument.multiplier,
            )
        },
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=option_delta)
        b.add_leg(account=routed(accounts, "market"), instrument=instrument, amount=-option_delta)
        cash_net = Decimal(0)
        for row in basket:
            if "instrument" in row:
                amount = to_decimal(row["quantity"], param="quantity") * qty * basket_side
                b.add_leg(
                    account=routed(accounts, "holdings"),
                    instrument=row["instrument"],
                    amount=amount,
                )
                b.add_leg(
                    account=routed(accounts, "market"),
                    instrument=row["instrument"],
                    amount=-amount,
                )
            else:
                cash_net += to_decimal(row["cash_amount"], param="cash_amount") * qty * basket_side
        # Physical settlement swaps basket for strike: they always move in
        # opposite directions, and C/P only picked the basket side above.
        cash_net -= basket_side * strike_cash
        if cash_net:
            b.add_leg(account=routed(accounts, "cash"), instrument=ccy, amount=cash_net)
            b.add_leg(account=routed(accounts, "market"), instrument=ccy, amount=-cash_net)
    assert b.transaction is not None
    return b.transaction


def exercise_option(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    contracts: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    override_deliverables: list[dict[str, Any]] | None = None,
    currency: Instrument | None = None,
    origin: str = "manual",
) -> Transaction:
    """Exercise a LONG option: the contracts leave the book; a call takes
    the basket in against the strike payment, a put delivers the basket
    against strike proceeds."""
    qty = to_decimal(contracts, param="contracts")
    meta = _option_meta(instrument)
    return _settle_option(
        kind="exercise",
        meta=meta,
        accounts=accounts,
        instrument=instrument,
        contracts=contracts,
        option_delta=-qty,
        basket_side=+1 if meta.right == "C" else -1,
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        override_deliverables=override_deliverables,
        currency=currency,
        origin=origin,
    )


def assign_option(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    contracts: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    override_deliverables: list[dict[str, Any]] | None = None,
    currency: Instrument | None = None,
    origin: str = "manual",
) -> Transaction:
    """Assignment on a SHORT option: the short position closes (+contracts
    back); a short call delivers the basket against strike proceeds, a
    short put takes the basket in against the strike payment."""
    qty = to_decimal(contracts, param="contracts")
    meta = _option_meta(instrument)
    return _settle_option(
        kind="assignment",
        meta=meta,
        accounts=accounts,
        instrument=instrument,
        contracts=contracts,
        option_delta=qty,
        basket_side=-1 if meta.right == "C" else +1,
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        override_deliverables=override_deliverables,
        currency=currency,
        origin=origin,
    )


def expire_option(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    contracts: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    """Worthless expiry: signed `contracts` matches the open position
    (positive closes a long, negative closes a short); no cash moves."""
    qty = to_decimal(contracts, param="contracts")
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description or f"expire {contracts} {instrument.code}",
        origin=origin,
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=-qty)
        b.add_leg(account=routed(accounts, "market"), instrument=instrument, amount=qty)
    assert b.transaction is not None
    return b.transaction
