"""Crypto lifecycle golden-leg tests (instruments spec §3.2).

The plan gave these no numbered milestone; they complete the spec's
crypto package surface. Same contract as every template (§4).
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Instrument
from django_assets.instruments.crypto import templates

pytestmark = pytest.mark.ledger

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def btc(usd):
    return Instrument.objects.create(
        code="BTC", quantity_decimals=8, price_decimals=2, price_currency=usd
    )


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "external", "commissions", "network_fees"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


def legs_by(tx):
    result = {}
    for leg in tx.legs.select_related("account", "instrument"):
        key = (leg.account.name, leg.instrument.code)
        assert key not in result, f"duplicate leg {key}"
        result[key] = leg.amount
    return result


def test_deposit_crypto(accounts, btc):
    tx = templates.deposit_crypto(
        accounts=accounts, instrument=btc, quantity="0.50000000", timestamp=TS
    )
    assert legs_by(tx) == {
        ("holdings", "BTC"): D("0.5"),
        ("external", "BTC"): D("-0.5"),
    }


def test_withdraw_crypto_with_network_fee(accounts, btc):
    """The fee is paid in kind and tracked like any other fee category."""
    tx = templates.withdraw_crypto(
        accounts=accounts,
        instrument=btc,
        quantity="0.20000000",
        network_fee="0.00010000",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "BTC"): D("-0.20010000"),
        ("network_fees", "BTC"): D("0.00010000"),
        ("external", "BTC"): D("0.20000000"),
    }


def test_buy_and_sell_crypto_share_the_trade_shape(accounts, usd, btc):
    buy = templates.buy_crypto(
        accounts=accounts,
        instrument=btc,
        quantity="0.10000000",
        price="90000.00",
        commission="4.50",
        timestamp=TS,
    )
    assert legs_by(buy) == {
        ("holdings", "BTC"): D("0.1"),
        ("external", "BTC"): D("-0.1"),
        ("cash", "USD"): D("-9004.50"),
        ("commissions", "USD"): D("4.50"),
        ("external", "USD"): D("9000.00"),
    }
    sell = templates.sell_crypto(
        accounts=accounts, instrument=btc, quantity="0.10000000", price="95000.00", timestamp=TS
    )
    assert legs_by(sell)[("cash", "USD")] == D("9500.00")


def test_staking_reward_and_airdrop(accounts, btc):
    reward = templates.staking_reward(
        accounts=accounts, instrument=btc, quantity="0.00012345", timestamp=TS
    )
    assert legs_by(reward)[("holdings", "BTC")] == D("0.00012345")
    assert "staking" in reward.description

    drop = templates.airdrop(accounts=accounts, instrument=btc, quantity="1.00000000", timestamp=TS)
    assert legs_by(drop)[("holdings", "BTC")] == D("1")
    assert "airdrop" in drop.description


def test_hard_fork(accounts, usd, btc):
    bch = Instrument.objects.create(code="BCH", quantity_decimals=8, price_currency=usd)
    tx = templates.hard_fork(
        accounts=accounts,
        instrument=btc,
        new_instrument=bch,
        quantity="0.50000000",
        timestamp=TS,
    )
    assert legs_by(tx) == {
        ("holdings", "BCH"): D("0.5"),
        ("external", "BCH"): D("-0.5"),
    }
    assert "BTC" in tx.description  # provenance in the description
