"""I2 golden-leg tests: buy/sell/short/cover (instruments spec §3.3).

The ADR-0020 HIMS decomposition is normative: net cash to the cash
account, fee components to user-owned tracking accounts, gross principal
to the consolidated external counterparty.
"""

from decimal import Decimal
from unittest import mock

import pytest

from django_assets.core.exceptions import ExcessPrecisionError
from django_assets.core.models import Transaction
from django_assets.instruments.equities import templates
from django_assets.instruments.exceptions import CapabilityError

from .conftest import TS, legs_by

pytestmark = pytest.mark.ledger

D = Decimal


def test_buy_shares_golden_legs(accounts, usd, aapl):
    tx = templates.buy_shares(
        accounts=accounts,
        instrument=aapl,
        quantity="100",
        price="175.50",
        commission="1.00",
        regulatory_fee="0.06",
        timestamp=TS,
    )
    assert isinstance(tx, Transaction)
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("100"),
        ("external", "AAPL"): D("-100"),
        ("cash", "USD"): D("-17551.06"),
        ("commissions", "USD"): D("1.00"),
        ("regulatory_fees", "USD"): D("0.06"),
        ("external", "USD"): D("17550.00"),
    }


def test_buy_shares_without_fees_has_four_legs(accounts, usd, aapl):
    tx = templates.buy_shares(
        accounts=accounts, instrument=aapl, quantity="10", price="100.00", timestamp=TS
    )
    assert tx.legs.count() == 4
    assert legs_by(tx)[("cash", "USD")] == D("-1000.00")


def test_sell_shares_golden_legs(accounts, usd, aapl):
    tx = templates.sell_shares(
        accounts=accounts,
        instrument=aapl,
        quantity="100",
        price="180.00",
        commission="1.00",
        regulatory_fee="0.06",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("-100"),
        ("external", "AAPL"): D("100"),
        ("cash", "USD"): D("17998.94"),
        ("commissions", "USD"): D("1.00"),
        ("regulatory_fees", "USD"): D("0.06"),
        ("external", "USD"): D("-18000.00"),
    }


def test_sub_cent_principal_requires_explicit_override(accounts, usd, aapl):
    """Computed qty×price off the currency grid must not round silently
    [D-5]; the caller passes the broker's own rounded principal instead."""
    with pytest.raises(ExcessPrecisionError):
        templates.buy_shares(
            accounts=accounts, instrument=aapl, quantity="3", price="33.3333", timestamp=TS
        )
    tx = templates.buy_shares(
        accounts=accounts,
        instrument=aapl,
        quantity="3",
        price="33.3333",
        principal="100.00",
        timestamp=TS,
    )
    assert legs_by(tx)[("cash", "USD")] == D("-100.00")


def test_float_rejected(accounts, usd, aapl):
    with pytest.raises(TypeError, match="Decimal"):
        templates.buy_shares(
            accounts=accounts,
            instrument=aapl,
            quantity=100.0,  # float-ok
            price="1.00",
            timestamp=TS,
        )


def test_origin_and_trade_timestamp_pass_through(accounts, usd, aapl):
    trade_ts = TS.replace(hour=15)
    tx = templates.buy_shares(
        accounts=accounts,
        instrument=aapl,
        quantity="1",
        price="100.00",
        timestamp=TS,
        trade_timestamp=trade_ts,
        origin="import:schwab",
    )
    assert tx.origin == "import:schwab"
    assert tx.trade_timestamp == trade_ts


def test_short_shares_mirrors_sell(accounts, usd, aapl):
    tx = templates.short_shares(
        accounts=accounts, instrument=aapl, quantity="50", price="200.00", timestamp=TS
    )
    assert legs_by(tx) == {
        ("holdings", "AAPL"): D("-50"),
        ("external", "AAPL"): D("50"),
        ("cash", "USD"): D("10000.00"),
        ("external", "USD"): D("-10000.00"),
    }


def test_cover_shares_mirrors_buy(accounts, usd, aapl):
    tx = templates.cover_shares(
        accounts=accounts, instrument=aapl, quantity="50", price="190.00", timestamp=TS
    )
    assert legs_by(tx)[("holdings", "AAPL")] == D("50")
    assert legs_by(tx)[("cash", "USD")] == D("-9500.00")


def test_short_shares_capability_advisory(accounts, usd, aapl):
    """D-46: consults AccountProfile via the lazy accessor. With brokerage
    absent (today) the advisory is a no-op; a profile with
    allows_short=False refuses."""
    # Absent: works (previous test). Present and disallowing: refuses.
    profile = mock.Mock(allows_short=False)
    with (
        mock.patch("django_assets.instruments.base.get_account_profile", return_value=profile),
        pytest.raises(CapabilityError, match="allows_short"),
    ):
        templates.short_shares(
            accounts=accounts, instrument=aapl, quantity="1", price="1.00", timestamp=TS
        )
    assert Transaction.objects.count() == 0


def test_missing_routing_key_is_a_clear_error(accounts, usd, aapl):
    del accounts["commissions"]
    with pytest.raises(KeyError, match="commissions"):
        templates.buy_shares(
            accounts=accounts,
            instrument=aapl,
            quantity="1",
            price="10.00",
            commission="1.00",
            timestamp=TS,
        )
