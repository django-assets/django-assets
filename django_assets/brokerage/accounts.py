"""Account-convention layer (brokerage spec §4.2).

ensure_standard_accounts builds the documented account set whose dict
shape plugs directly into instruments' templates (the routing-key
convention in django_assets.instruments.base.ROUTING_KEYS). Tracking
balances answer report questions directly:
Holding.current(accounts["commissions"], usd) is lifetime commissions.
"""

from typing import Any, cast

from django.contrib.auth.base_user import AbstractBaseUser

from django_assets.brokerage.models import AccountProfile
from django_assets.core.models import Account

#: routing key -> default account name [D-14]
DEFAULT_ACCOUNT_NAMES = {
    "cash": "brokerage_cash",
    "holdings": "brokerage_holdings",
    "external": "external_counterparty",
    "commissions": "commissions_paid",
    "regulatory_fees": "regulatory_fees_paid",
    "adr_fees": "adr_fees_paid",
    "tax_withheld": "tax_withheld",
    "foreign_tax": "foreign_tax_paid",
    "interest": "interest_earned",
    "network_fees": "network_fees_paid",
    "account_fees": "account_fees_paid",
    "wire_fees": "wire_fees_paid",
    "margin_interest": "margin_interest_paid",
}


def ensure_standard_accounts(
    user: AbstractBaseUser, naming: dict[str, str] | None = None
) -> dict[str, Account]:
    """Create-or-get the recommended account set for `user` [D-14].

    Idempotent; `naming` overrides individual keys. The returned mapping
    is the templates' `accounts=` argument.
    """
    names = {**DEFAULT_ACCOUNT_NAMES, **(naming or {})}
    # cast: the FK targets settings.AUTH_USER_MODEL — the stubs resolve it
    # to the dev project's concrete User, but this is host-generic API.
    owner = cast(Any, user)
    return {
        key: Account.objects.get_or_create(owner=owner, name=name)[0] for key, name in names.items()
    }


def account_allows_reconciliation(account: Account) -> bool:
    """The single accessor for "is this account broker-reported?" [D-10].

    Missing profile means False — never a RelatedObjectDoesNotExist.
    """
    profile = AccountProfile.objects.filter(account=account).first()
    return profile is not None and profile.allows_reconciliation
