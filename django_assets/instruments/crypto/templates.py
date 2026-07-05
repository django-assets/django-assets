"""Crypto lifecycle templates (instruments spec §3.2; contract §4).

deposit/withdraw move coins across the wallet boundary (network fees
paid in kind, tracked like any fee category); buy/sell reuse the shared
trade shape; staking rewards, airdrops, and hard forks are in-kind
receipts with no cash side.
"""

import datetime
from decimal import Decimal
from typing import Any

from django_assets.core.builder import TransactionBuilder
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.instruments.base import routed
from django_assets.instruments.base import share_trade as _share_trade

Amount = Decimal | int | str
AccountMap = dict[str, Account]


def _in_kind(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Decimal,
    description: str,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None,
    origin: str,
    via: str,
) -> Transaction:
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=description,
        origin=origin,
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=quantity)
        b.add_leg(account=routed(accounts, via), instrument=instrument, amount=-quantity)
    assert b.transaction is not None
    return b.transaction


def deposit_crypto(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    qty = to_decimal(quantity, param="quantity")
    return _in_kind(
        accounts=accounts,
        instrument=instrument,
        quantity=qty,
        description=f"deposit {quantity} {instrument.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
        via="funding",
    )


def withdraw_crypto(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    network_fee: Amount = 0,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    """`quantity` is what arrives outside; the network fee leaves the
    wallet too, in kind, and lands in the tracking account."""
    qty = to_decimal(quantity, param="quantity")
    fee = to_decimal(network_fee, param="network_fee")
    with TransactionBuilder(
        account=routed(accounts, "holdings"),
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        description=f"withdraw {quantity} {instrument.code}",
        origin=origin,
    ) as b:
        b.add_leg(account=routed(accounts, "holdings"), instrument=instrument, amount=-(qty + fee))
        if fee:
            b.add_leg(account=routed(accounts, "network_fees"), instrument=instrument, amount=fee)
        b.add_leg(account=routed(accounts, "funding"), instrument=instrument, amount=qty)
    assert b.transaction is not None
    return b.transaction


def buy_crypto(**kwargs: Any) -> Transaction:
    kwargs.setdefault("description", f"buy {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=+1, **kwargs)


def sell_crypto(**kwargs: Any) -> Transaction:
    kwargs.setdefault("description", f"sell {kwargs['quantity']} {kwargs['instrument'].code}")
    return _share_trade(side=-1, **kwargs)


def staking_reward(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    return _in_kind(
        accounts=accounts,
        instrument=instrument,
        quantity=to_decimal(quantity, param="quantity"),
        description=f"staking reward {quantity} {instrument.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
        via="issuers",
    )


def airdrop(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    return _in_kind(
        accounts=accounts,
        instrument=instrument,
        quantity=to_decimal(quantity, param="quantity"),
        description=f"airdrop {quantity} {instrument.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
        via="issuers",
    )


def hard_fork(
    *,
    accounts: AccountMap,
    instrument: Instrument,
    new_instrument: Instrument,
    quantity: Amount,
    timestamp: datetime.datetime,
    trade_timestamp: datetime.datetime | None = None,
    origin: str = "manual",
) -> Transaction:
    """Receive the new chain's coins; the original position is untouched
    (a fork is not a disposal)."""
    return _in_kind(
        accounts=accounts,
        instrument=new_instrument,
        quantity=to_decimal(quantity, param="quantity"),
        description=f"hard fork of {instrument.code}: receive {quantity} {new_instrument.code}",
        timestamp=timestamp,
        trade_timestamp=trade_timestamp,
        origin=origin,
        via="issuers",
    )
