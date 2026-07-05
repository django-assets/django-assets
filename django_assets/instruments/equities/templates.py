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
    routed,
)
from django_assets.instruments.base import (
    share_trade as _share_trade,
)

Amount = Decimal | int | str
AccountMap = dict[str, Account]


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
    character: str = "unclassified",
    character_label: str = "",
    character_source: str = "broker",
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
    from django_assets.core.income import income_character_metadata

    metadata = {
        **(metadata or {}),
        **income_character_metadata(character, character_label, character_source),
        # ADR-0037 event detection: which instrument produced this income
        "income_instrument_id": instrument.pk,
    }
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
        b.add_leg(account=routed(accounts, "issuers"), instrument=ccy, amount=-gross)
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


def return_of_capital(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    amount: Amount,
    currency: Instrument | None = None,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    metadata: dict[str, Any] | None = None,
) -> Transaction:
    """ADR-0038 §3: a nondividend distribution — cash arrives but it is
    NOT income; it returns basis. No income tracker leg. The lots
    rebuild consumes the metadata tag: pro-rata basis reduction across
    the then-open lots at trade date, excess over remaining basis
    emitting a capital-gain match (zero-quantity LotMatch — basis
    recovery is the conservation law's primitive, so no DDL changes)."""
    from django_assets.core.income import income_character_metadata

    ccy = cash_currency(instrument, currency)
    value = to_decimal(amount, param="amount")
    tag_metadata = {
        **(metadata or {}),
        **income_character_metadata("return_of_capital", source="broker"),
        "return_of_capital": {
            "instrument_id": instrument.pk,
            "instrument": instrument.code,
            "amount": str(value),
        },
    }
    with TransactionBuilder(
        account=routed(accounts, "cash"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description or f"return of capital {instrument.code}",
        origin=origin,
        metadata=tag_metadata,
    ) as b:
        b.add_leg(account=routed(accounts, "cash"), instrument=ccy, amount=value)
        b.add_leg(account=routed(accounts, "issuers"), instrument=ccy, amount=-value)
    assert b.transaction is not None
    return b.transaction


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
        legs=[("holdings", instrument, qty), ("issuers", instrument, -qty)],
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
        legs=[("holdings", instrument, -qty), ("issuers", instrument, qty)],
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
        legs=[("holdings", instrument, qty), ("issuers", instrument, -qty)],
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
        legs=[("holdings", new_instrument, qty), ("issuers", new_instrument, -qty)],
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
            ("issuers", instrument, qty),
            ("holdings", new_instrument, new_qty),
            ("issuers", new_instrument, -new_qty),
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
        legs=[("holdings", rights_instrument, qty), ("issuers", rights_instrument, -qty)],
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
            ("issuers", warrant_instrument, qty),
            ("holdings", instrument, qty),
            ("issuers", instrument, -qty),
            ("cash", ccy, -paid),
            ("issuers", ccy, paid),
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
        b.add_leg(account=routed(accounts, "issuers"), instrument=from_instrument, amount=from_qty)
        b.add_leg(account=routed(accounts, "holdings"), instrument=to_instrument, amount=to_qty)
        b.add_leg(account=routed(accounts, "issuers"), instrument=to_instrument, amount=-to_qty)
    assert b.transaction is not None
    return b.transaction
