"""B3 golden-leg tests: cash + transfers + standalone fees (spec §4.3).

Every template is an atomic ledger constructor committing under the
trigger. Standalone fees produce their own Transactions (ADR-0021).
Income trackers accumulate negative, expense trackers positive — both
directions answer report questions via Holding.current.
"""

from decimal import Decimal

import pytest

from django_assets.brokerage import templates
from django_assets.core.models import Account, Instrument, Transaction

from .conftest import TS, legs_by

pytestmark = pytest.mark.ledger

D = Decimal


def test_deposit_currency(accounts, usd):
    tx = templates.deposit_currency(accounts=accounts, currency=usd, amount="5000.00", timestamp=TS)
    assert legs_by(tx) == {
        ("brokerage_cash", "USD"): D("5000.00"),
        ("external_counterparty", "USD"): D("-5000.00"),
    }
    assert tx.origin == "manual"


def test_withdraw_currency(accounts, usd):
    tx = templates.withdraw_currency(
        accounts=accounts, currency=usd, amount="1200.00", timestamp=TS
    )
    assert legs_by(tx) == {
        ("brokerage_cash", "USD"): D("-1200.00"),
        ("external_counterparty", "USD"): D("1200.00"),
    }


def test_transfer_currency_between_own_accounts(accounts, usd, user):
    savings = Account.objects.create(owner=user, name="savings")
    tx = templates.transfer_currency(
        from_account=accounts["cash"],
        to_account=savings,
        currency=usd,
        amount="300.00",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("brokerage_cash", "USD"): D("-300.00"),
        ("savings", "USD"): D("300.00"),
    }


def test_interest_earned_accumulates_negative_in_tracker(accounts, usd):
    """Income tracker: Holding.current(accounts['interest'], usd) reports
    lifetime interest as a negative (credit) balance."""
    tx = templates.interest_earned(accounts=accounts, currency=usd, amount="12.34", timestamp=TS)
    assert legs_by(tx) == {
        ("brokerage_cash", "USD"): D("12.34"),
        ("interest_earned", "USD"): D("-12.34"),
    }


def test_standalone_fee_templates_each_own_transaction(accounts, usd):
    """ADR-0021: independently-posted fees are their own Transactions."""
    cases = [
        (templates.commission_charged, "commissions_paid"),
        (templates.account_fee, "account_fees_paid"),
        (templates.wire_fee, "wire_fees_paid"),
        (templates.regulatory_fee, "regulatory_fees_paid"),
        (templates.adr_fee_deducted, "adr_fees_paid"),
    ]
    for template, tracker in cases:
        tx = template(accounts=accounts, currency=usd, amount="2.50", timestamp=TS)
        assert legs_by(tx) == {
            ("brokerage_cash", "USD"): D("-2.50"),
            (tracker, "USD"): D("2.50"),
        }, tracker
    assert Transaction.objects.count() == len(cases)


def test_transfer_asset(accounts, usd, user):
    aapl = Instrument.objects.create(code="AAPL", quantity_decimals=0, price_currency=usd)
    ira = Account.objects.create(owner=user, name="ira_holdings")
    tx = templates.transfer_asset(
        from_account=accounts["holdings"],
        to_account=ira,
        instrument=aapl,
        quantity="25",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("brokerage_holdings", "AAPL"): D("-25"),
        ("ira_holdings", "AAPL"): D("25"),
    }


def test_intake_guard_and_origin_pass_through(accounts, usd):
    with pytest.raises(TypeError, match="Decimal"):
        templates.deposit_currency(
            accounts=accounts,
            currency=usd,
            amount=100.0,  # float-ok
            timestamp=TS,
        )
    trade_ts = TS.replace(hour=12)
    tx = templates.deposit_currency(
        accounts=accounts,
        currency=usd,
        amount="1.00",
        timestamp=TS,
        trade_timestamp=trade_ts,
        origin="import:schwab",
    )
    assert tx.origin == "import:schwab"
    assert tx.trade_timestamp == trade_ts


def test_transfer_between_owners_refused(accounts, usd, user):
    from django.contrib.auth import get_user_model

    from django_assets.core.exceptions import MixedOwnershipError

    other = get_user_model().objects.create_user(username="other", password="x")
    foreign = Account.objects.create(owner=other, name="foreign")
    with pytest.raises(MixedOwnershipError):
        templates.transfer_currency(
            from_account=accounts["cash"],
            to_account=foreign,
            currency=usd,
            amount="1.00",
            timestamp=TS,
        )
