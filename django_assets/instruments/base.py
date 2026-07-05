"""Shared template plumbing (instruments spec §4; cross-type, so it lives
at the package root per ADR-0033 §1)."""

import datetime
from decimal import Decimal
from typing import Any

from django.apps import apps

from django_assets.core.builder import TransactionBuilder
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.instruments.exceptions import CapabilityError

Amount = Decimal | int | str
AccountMap = dict[str, Account]

#: The documented routing-key convention. Brokerage's
#: ensure_standard_accounts (D-14) produces a dict with these keys.
ROUTING_KEYS = (
    "cash",
    "holdings",
    "market",
    "funding",
    "issuers",
    "conversions",
    "commissions",
    "regulatory_fees",
    "tax_withheld",
    "foreign_tax",
    "network_fees",
    "account_fees",
    "wire_fees",
    "adr_fees",
    "interest",
    "margin_interest",
)


def routed(accounts: dict[str, Account], key: str) -> Account:
    try:
        return accounts[key]
    except KeyError:
        raise KeyError(
            f"account routing key {key!r} missing — templates route legs by the "
            f"documented convention keys {ROUTING_KEYS}; pass an Account under "
            f"{key!r} (ensure_standard_accounts builds the full set)"
        ) from None


def cash_currency(instrument: Instrument, currency: Instrument | None) -> Instrument:
    resolved = currency if currency is not None else instrument.price_currency
    if resolved is None:
        raise ValueError(f"{instrument.code!r} has no price_currency; pass currency= explicitly")
    return resolved


def principal_amount(
    currency: Instrument,
    quantity: Decimal | int | str,
    price: Decimal | int | str,
    multiplier: Decimal,
    principal: Decimal | int | str | None,
) -> Decimal:
    """Gross principal: explicit override, else qty × price × multiplier
    quantized STRICTLY to the currency — if the product falls off the
    cash grid the caller must pass the broker's own rounded principal
    (source-shape fidelity beats silent rounding, D-5)."""
    if principal is not None:
        return to_decimal(principal, param="principal")
    computed = to_decimal(quantity, param="quantity") * to_decimal(price, param="price")
    return currency.quantize(computed * multiplier, strict=True)


def get_account_profile(account: Account) -> Any | None:
    """D-46 lazy accessor: consult brokerage's AccountProfile when that
    milestone exists, degrade to None (advisory no-op) when it doesn't —
    instruments carries no brokerage import."""
    try:
        model = apps.get_model("django_assets", "AccountProfile")
    except LookupError:
        return None
    return model.objects.filter(account=account).first()


def check_capability(account: Account, flag: str, operation: str) -> None:
    profile = get_account_profile(account)
    if profile is not None and getattr(profile, flag, True) is False:
        raise CapabilityError(
            f"{operation} refused: AccountProfile.{flag} is False for "
            f"account {account.name!r} (advisory, ADR-0014)"
        )


def share_trade(
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
        b.add_leg(account=routed(accounts, "market"), instrument=instrument, amount=-side * qty)
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
        b.add_leg(account=routed(accounts, "market"), instrument=ccy, amount=side * gross)
    assert b.transaction is not None
    return b.transaction
