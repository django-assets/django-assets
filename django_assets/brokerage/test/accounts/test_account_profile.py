"""B1: AccountProfile + account conventions (brokerage spec §2, ADR-0014).

Capability flags are advisory (templates MAY consult them; the ledger
never does); allows_reconciliation is brokerage-enforced via a pre_save
guard. Missing profile = allows_reconciliation False (D-10).
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.brokerage.accounts import (
    account_allows_reconciliation,
    ensure_standard_accounts,
)
from django_assets.brokerage.models import AccountProfile
from django_assets.core.models import Account

pytestmark = pytest.mark.django_db

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def account(user):
    return Account.objects.create(owner=user, name="brokerage")


def test_profile_defaults_are_false(account):
    profile = AccountProfile.objects.create(account=account)
    assert profile.allows_short is False
    assert profile.allows_margin is False
    assert profile.is_tax_advantaged is False
    assert profile.allows_reconciliation is False
    assert profile.subtype == ""
    assert account.brokerage_profile == profile


def test_subtype_vocabulary_not_db_enforced(account):
    """ADR-0014: recommended vocabulary, host-extensible — no ENUM, no CHECK."""
    profile = AccountProfile.objects.create(account=account, subtype="my_custom_subtype")
    profile.refresh_from_db()
    assert profile.subtype == "my_custom_subtype"


def test_account_allows_reconciliation_helper(account, user):
    """D-10: the single accessor; profile-less accounts are False, never
    a RelatedObjectDoesNotExist."""
    assert account_allows_reconciliation(account) is False  # no profile
    profile = AccountProfile.objects.create(account=account)
    assert account_allows_reconciliation(account) is False  # flag default
    profile.allows_reconciliation = True
    profile.save()
    assert account_allows_reconciliation(account) is True


def test_clearing_reconciliation_without_matches_succeeds(account):
    """The pre_save guard only refuses while matched_legs reference the
    account's legs; with none (ImportLine arrives in B4), clearing works."""
    profile = AccountProfile.objects.create(account=account, allows_reconciliation=True)
    profile.allows_reconciliation = False
    profile.save()
    profile.refresh_from_db()
    assert profile.allows_reconciliation is False


def test_ensure_standard_accounts_idempotent(user):
    """D-14: creates the documented set once; second call returns the
    same rows."""
    first = ensure_standard_accounts(user)
    assert set(first) >= {
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
    }
    assert all(acct.owner == user for acct in first.values())
    second = ensure_standard_accounts(user)
    assert {k: a.pk for k, a in first.items()} == {k: a.pk for k, a in second.items()}


def test_ensure_standard_accounts_naming_override(user):
    accounts = ensure_standard_accounts(user, naming={"cash": "my_cash"})
    assert accounts["cash"].name == "my_cash"
    assert accounts["holdings"].name != "my_cash"  # defaults still apply


def test_ensure_standard_accounts_feeds_templates(user):
    """The returned dict plugs straight into instruments' templates."""
    from django_assets.core.models import Instrument
    from django_assets.instruments.equities import templates

    accounts = ensure_standard_accounts(user)
    usd = Instrument.objects.create(code="USD", quantity_decimals=2)
    aapl = Instrument.objects.create(code="AAPL", quantity_decimals=0, price_currency=usd)
    tx = templates.buy_shares(
        accounts=accounts, instrument=aapl, quantity="1", price="100.00", timestamp=TS
    )
    assert tx.pk is not None


def test_short_capability_advisory_end_to_end(user):
    """With brokerage present, the D-46 lazy accessor finds the real
    profile: allows_short=False refuses, True proceeds."""
    from django_assets.core.models import Instrument
    from django_assets.instruments.equities import templates
    from django_assets.instruments.exceptions import CapabilityError

    accounts = ensure_standard_accounts(user)
    AccountProfile.objects.create(account=accounts["holdings"], allows_short=False)
    usd = Instrument.objects.create(code="USD", quantity_decimals=2)
    aapl = Instrument.objects.create(code="AAPL", quantity_decimals=0, price_currency=usd)
    with pytest.raises(CapabilityError, match="allows_short"):
        templates.short_shares(
            accounts=accounts, instrument=aapl, quantity="1", price="100.00", timestamp=TS
        )
    AccountProfile.objects.filter(account=accounts["holdings"]).update(allows_short=True)
    tx = templates.short_shares(
        accounts=accounts, instrument=aapl, quantity="1", price="100.00", timestamp=TS
    )
    assert tx.pk is not None
