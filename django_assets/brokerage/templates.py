"""Workflow templates, wave 1: cash + transfers + standalone fees
(brokerage spec §4.3, ADR-0021).

Plumbing only — type-specific lifecycle templates live in
django_assets.instruments per ADR-0033. Standalone fees are their own
Transactions. Tracking-account convention: expense trackers accumulate
positive balances, income trackers negative; either way
Holding.current(tracker, currency) answers the report question.
"""

import datetime
from decimal import Decimal

from django_assets.core.builder import TransactionBuilder
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.instruments.base import AccountMap, routed

Amount = Decimal | int | str


def _two_leg(
    *,
    debit: Account,
    credit: Account,
    instrument: Instrument,
    amount: Decimal,
    perspective: Account,
    description: str,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None,
    origin: str,
) -> Transaction:
    with TransactionBuilder(
        account=perspective,
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    ) as b:
        b.add_leg(account=debit, instrument=instrument, amount=amount)
        b.add_leg(account=credit, instrument=instrument, amount=-amount)
    assert b.transaction is not None
    return b.transaction


def deposit_currency(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    via: str = "funding",
) -> Transaction:
    """`via` names the world-side routing key (ADR-0035): "funding" for
    the owner's own money, "conversions" for the receiving side of a
    currency exchange."""
    value = to_decimal(amount, param="amount")
    return _two_leg(
        debit=routed(accounts, "cash"),
        credit=routed(accounts, via),
        instrument=currency,
        amount=value,
        perspective=routed(accounts, "cash"),
        description=description or f"deposit {amount} {currency.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def withdraw_currency(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    via: str = "funding",
) -> Transaction:
    """`via` names the world-side routing key (ADR-0035): "funding" for
    the owner's own money, "conversions" for the paying side of a
    currency exchange."""
    value = to_decimal(amount, param="amount")
    return _two_leg(
        debit=routed(accounts, via),
        credit=routed(accounts, "cash"),
        instrument=currency,
        amount=value,
        perspective=routed(accounts, "cash"),
        description=description or f"withdraw {amount} {currency.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def transfer_currency(
    *,
    from_account: Account,
    to_account: Account,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    """Between two accounts of ONE owner — the builder's D-3 invariant
    refuses cross-owner transfers."""
    value = to_decimal(amount, param="amount")
    return _two_leg(
        debit=to_account,
        credit=from_account,
        instrument=currency,
        amount=value,
        perspective=from_account,
        description=description or f"transfer {amount} {currency.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def transfer_asset(
    *,
    from_account: Account,
    to_account: Account,
    instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    value = to_decimal(quantity, param="quantity")
    return _two_leg(
        debit=to_account,
        credit=from_account,
        instrument=instrument,
        amount=value,
        perspective=from_account,
        description=description or f"transfer {quantity} {instrument.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def interest_earned(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    """Income tracker accumulates negative (credit) balances."""
    value = to_decimal(amount, param="amount")
    return _two_leg(
        debit=routed(accounts, "cash"),
        credit=routed(accounts, "interest"),
        instrument=currency,
        amount=value,
        perspective=routed(accounts, "cash"),
        description=description or f"interest earned {amount} {currency.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def _fee_template(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    tracker_key: str,
    kind: str,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None,
    description: str,
    origin: str,
) -> Transaction:
    value = to_decimal(amount, param="amount")
    return _two_leg(
        debit=routed(accounts, tracker_key),
        credit=routed(accounts, "cash"),
        instrument=currency,
        amount=value,
        perspective=routed(accounts, "cash"),
        description=description or f"{kind} {amount} {currency.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
    )


def commission_charged(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="commissions",
        kind="commission",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def account_fee(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="account_fees",
        kind="account fee",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def wire_fee(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="wire_fees",
        kind="wire fee",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def regulatory_fee(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="regulatory_fees",
        kind="regulatory fee",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def adr_fee_deducted(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="adr_fees",
        kind="ADR fee",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def interest_charged(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    """Margin/debit interest paid (expense tracker accumulates +)."""
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key="margin_interest",
        kind="interest charged",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def tax_withholding(
    *,
    accounts: AccountMap,
    currency: Instrument,
    amount: Amount,
    tracker_key: str = "tax_withheld",
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
) -> Transaction:
    """Standalone withholding row (broker posts tax separately from the
    income event): cash out, tracking account accumulates the paid tax."""
    return _fee_template(
        accounts=accounts,
        currency=currency,
        amount=amount,
        tracker_key=tracker_key,
        kind="tax withheld",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    )


def quantity_adjustment(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    description: str = "",
    origin: str = "manual",
    metadata: "dict[str, object] | None" = None,
) -> Transaction:
    """Signed in-kind position adjustment against the counterparty:
    transfers in/out, journaled shares, merger legs without a pairing —
    the inventory's quantity_adjustment (ADR-0019 checklist)."""
    value = to_decimal(quantity, param="quantity")
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description or f"quantity adjustment {quantity} {instrument.code}",
        origin=origin,
        metadata=metadata,
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=value)
        b.add_leg(account=routed(accounts, "funding"), instrument=instrument, amount=-value)
    assert b.transaction is not None
    return b.transaction
