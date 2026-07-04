"""Equity lifecycle templates (instruments spec §3.3; contract §4).

Atomic ledger constructors via core's TransactionBuilder. The ADR-0020
HIMS decomposition is normative: net cash to `cash`, fee components to
user-owned tracking accounts, gross principal to the consolidated
`external` counterparty. Corporate-action and conversion templates write
the ADR-0032 tags the lots rebuild interprets.
"""

import datetime
from decimal import Decimal
from typing import Any

from django_assets.core.builder import TransactionBuilder
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.instruments import tags
from django_assets.instruments.base import (
    cash_currency,
    check_capability,
    principal_amount,
    routed,
)

Amount = Decimal | int | str
AccountMap = dict[str, Account]


def _share_trade(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    price: Amount,
    side: int,  # +1 = shares in (buy/cover), -1 = shares out (sell/short)
    commission: Amount = 0,
    regulatory_fee: Amount = 0,
    principal: Amount | None = None,
    currency: Instrument | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    ccy = cash_currency(instrument, currency)
    qty = to_decimal(quantity, param="quantity")
    gross = principal_amount(ccy, qty, price, instrument.multiplier, principal)
    fee_commission = to_decimal(commission, param="commission")
    fee_regulatory = to_decimal(regulatory_fee, param="regulatory_fee")
    # Net cash from the user's perspective: buys pay principal + fees,
    # sells receive principal − fees (HIMS shape).
    net_cash = -side * gross - fee_commission - fee_regulatory

    with TransactionBuilder(
        account=routed(accounts, "cash"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
        metadata=metadata,
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=side * qty)
        b.add_leg(account=routed(accounts, "external"), instrument=instrument, amount=-side * qty)
        b.add_leg(account=routed(accounts, "cash"), instrument=ccy, amount=net_cash)
        if fee_commission:
            b.add_leg(
                account=routed(accounts, "commissions"), instrument=ccy, amount=fee_commission
            )
        if fee_regulatory:
            b.add_leg(
                account=routed(accounts, "regulatory_fees"),
                instrument=ccy,
                amount=fee_regulatory,
            )
        b.add_leg(account=routed(accounts, "external"), instrument=ccy, amount=side * gross)
    assert b.transaction is not None
    return b.transaction


def buy_shares(**kwargs: Any) -> Transaction:
    kwargs.setdefault("description", f"buy {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=+1, **kwargs)


def sell_shares(**kwargs: Any) -> Transaction:
    kwargs.setdefault("description", f"sell {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=-1, **kwargs)


def short_shares(**kwargs: Any) -> Transaction:
    """Sell shares not held. Advisory capability check (D-46/ADR-0014):
    refuses when an AccountProfile exists with allows_short=False."""
    check_capability(routed(kwargs["accounts"], "holdings"), "allows_short", "short_shares")
    kwargs.setdefault("description", f"short {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=-1, **kwargs)


def cover_shares(**kwargs: Any) -> Transaction:
    kwargs.setdefault("description", f"cover {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=+1, **kwargs)


def _cash_distribution(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    amount: Amount,
    kind: str,
    tax_withheld: Amount = 0,
    tax_key: str = "tax_withheld",
    currency: Instrument | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    ccy = cash_currency(instrument, currency)
    gross = to_decimal(amount, param="amount")
    withheld = to_decimal(tax_withheld, param="tax_withheld")
    with TransactionBuilder(
        account=routed(accounts, "cash"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description or f"{kind} {instrument.code}",
        origin=origin,
        metadata=metadata,
    ) as b:
        b.add_leg(account=routed(accounts, "cash"), instrument=ccy, amount=gross - withheld)
        if withheld:
            b.add_leg(account=routed(accounts, tax_key), instrument=ccy, amount=withheld)
        b.add_leg(account=routed(accounts, "external"), instrument=ccy, amount=-gross)
    assert b.transaction is not None
    return b.transaction


def dividend_received(**kwargs: Any) -> Transaction:
    return _cash_distribution(kind="dividend", **kwargs)


def dividend_received_with_tax(**kwargs: Any) -> Transaction:
    return _cash_distribution(kind="dividend", tax_key="tax_withheld", **kwargs)


def foreign_dividend_received(**kwargs: Any) -> Transaction:
    return _cash_distribution(kind="foreign dividend", tax_key="foreign_tax", **kwargs)


def capital_gain_distribution(**kwargs: Any) -> Transaction:
    return _cash_distribution(kind="capital gain distribution", **kwargs)


def dividend_reinvested(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    amount: Amount,
    quantity: Amount,
    currency: Instrument | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> tuple[Transaction, Transaction]:
    """DRIP = two Transactions (dividend, then purchase) — brokers post
    two lines (ADR-0021 source shape), and lots opens the new lot from a
    real purchase leg at the cash basis."""
    dividend = dividend_received(
        accounts=accounts,
        instrument=instrument,
        amount=amount,
        currency=currency,
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"dividend {instrument.code} (reinvested)",
        origin=origin,
    )
    purchase = buy_shares(
        accounts=accounts,
        instrument=instrument,
        quantity=quantity,
        price="0",
        principal=amount,
        currency=currency,
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"reinvest {quantity} {instrument.code}",
        origin=origin,
    )
    return dividend, purchase


def _corporate_action(
    *,
    accounts: AccountMap,
    tag: dict[str, int | str],
    legs: list[tuple[str, Instrument, Decimal]],
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None,
    description: str,
    origin: str,
) -> Transaction:
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
        metadata={"corporate_action": tag},
    ) as b:
        for key, instrument, amount in legs:
            b.add_leg(account=routed(accounts, key), instrument=instrument, amount=amount)
    assert b.transaction is not None
    return b.transaction


def stock_split(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    additional_quantity: Amount,
    ratio: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(additional_quantity, param="additional_quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag("split", instrument, ratio=to_decimal(ratio, param="ratio")),
        legs=[("holdings", instrument, qty), ("external", instrument, -qty)],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"{ratio}:1 split {instrument.code}",
        origin=origin,
    )


def reverse_split(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    removed_quantity: Amount,
    ratio: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(removed_quantity, param="removed_quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "reverse_split", instrument, ratio=to_decimal(ratio, param="ratio")
        ),
        legs=[("holdings", instrument, -qty), ("external", instrument, qty)],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"reverse split {instrument.code}",
        origin=origin,
    )


def stock_dividend(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    additional_quantity: Amount,
    ratio: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(additional_quantity, param="additional_quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "stock_dividend", instrument, ratio=to_decimal(ratio, param="ratio")
        ),
        legs=[("holdings", instrument, qty), ("external", instrument, -qty)],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"stock dividend {instrument.code}",
        origin=origin,
    )


def spinoff(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    new_instrument: Instrument,
    quantity: Amount,
    ratio: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(quantity, param="quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "spinoff",
            instrument,
            ratio=to_decimal(ratio, param="ratio"),
            new_instrument_id=new_instrument.pk,
        ),
        legs=[("holdings", new_instrument, qty), ("external", new_instrument, -qty)],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"spinoff {new_instrument.code} from {instrument.code}",
        origin=origin,
    )


def merger_exchange(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    new_instrument: Instrument,
    quantity: Amount,
    new_quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(quantity, param="quantity")
    new_qty = to_decimal(new_quantity, param="new_quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "merger_exchange", instrument, new_instrument_id=new_instrument.pk
        ),
        legs=[
            ("holdings", instrument, -qty),
            ("external", instrument, qty),
            ("holdings", new_instrument, new_qty),
            ("external", new_instrument, -new_qty),
        ],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"merger: {instrument.code} into {new_instrument.code}",
        origin=origin,
    )


def rights_offering(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    rights_instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(quantity, param="quantity")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "rights_offering", instrument, rights_instrument_id=rights_instrument.pk
        ),
        legs=[("holdings", rights_instrument, qty), ("external", rights_instrument, -qty)],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"rights offering {instrument.code}",
        origin=origin,
    )


def warrant_exercise(
    *,
    accounts: AccountMap,
    warrant_instrument: Instrument,
    instrument: Instrument,
    quantity: Amount,
    cost: Amount,
    currency: Instrument | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    ccy = cash_currency(instrument, currency)
    qty = to_decimal(quantity, param="quantity")
    paid = to_decimal(cost, param="cost")
    return _corporate_action(
        accounts=accounts,
        tag=tags.corporate_action_tag(
            "warrant_exercise", instrument, warrant_instrument_id=warrant_instrument.pk
        ),
        legs=[
            ("holdings", warrant_instrument, -qty),
            ("external", warrant_instrument, qty),
            ("holdings", instrument, qty),
            ("external", instrument, -qty),
            ("cash", ccy, -paid),
            ("external", ccy, paid),
        ],
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"exercise {quantity} {warrant_instrument.code}",
        origin=origin,
    )


def convert_instrument(
    *,
    accounts: AccountMap,
    from_instrument: Instrument,
    to_instrument: Instrument,
    from_quantity: Amount,
    to_quantity: Amount,
    ratio: Amount | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    """The ADR-0009 four-leg DTC conversion (CEDEAR ↔ ordinary): quantity
    changes form, basis carries over (lots reads the §5 tag [D-45]); no
    cash, no realized result."""
    from_qty = to_decimal(from_quantity, param="from_quantity")
    to_qty = to_decimal(to_quantity, param="to_quantity")
    if ratio is not None:
        expected = to_decimal(ratio, param="ratio") * to_qty
        if from_qty != expected:
            raise ValueError(
                f"conversion ratio check failed: {from_qty} {from_instrument.code} "
                f"≠ ratio {ratio} × {to_qty} {to_instrument.code}"
            )
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"convert {from_quantity} {from_instrument.code} "
        f"to {to_quantity} {to_instrument.code}",
        origin=origin,
        metadata={
            "conversion": tags.conversion_tag(
                from_instrument=from_instrument,
                to_instrument=to_instrument,
                from_quantity=from_qty,
                to_quantity=to_qty,
            )
        },
    ) as b:
        b.add_leg(
            account=routed(accounts, "holdings"), instrument=from_instrument, amount=-from_qty
        )
        b.add_leg(account=routed(accounts, "external"), instrument=from_instrument, amount=from_qty)
        b.add_leg(account=routed(accounts, "holdings"), instrument=to_instrument, amount=to_qty)
        b.add_leg(account=routed(accounts, "external"), instrument=to_instrument, amount=-to_qty)
    assert b.transaction is not None
    return b.transaction
