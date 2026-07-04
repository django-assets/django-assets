"""C3: TransactionBuilder — spec §4.1.

Context manager building one balanced Transaction atomically: intake guards
(PADR-0006 Rule 3), strict quantization [D-5], same-owner invariant [D-3],
and the Python zero-sum fallback when DJANGO_ASSETS_USE_DB_TRIGGERS=False.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings

from django_assets.core.builder import TransactionBuilder
from django_assets.core.exceptions import (
    ExcessPrecisionError,
    MixedOwnershipError,
    UnbalancedTransactionError,
)
from django_assets.core.models import Account, Transaction, TransactionLeg

pytestmark = pytest.mark.ledger

TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)
D = Decimal


def test_happy_path_persists_quantized_legs(accounts, usd, aapl):
    with TransactionBuilder(
        account=accounts["cash"], timestamp=TS, description="buy 100 AAPL"
    ) as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-17550.56")
        b.add_leg(account=accounts["holdings"], instrument=aapl, amount=100)
        b.add_leg(account=accounts["external"], instrument=usd, amount=D("17550.56"))
        b.add_leg(account=accounts["external"], instrument=aapl, amount=D("-100"))

    tx = b.transaction
    assert tx is not None and tx.pk is not None
    assert tx.description == "buy 100 AAPL"
    assert tx.origin == "manual"
    legs = list(tx.legs.order_by("id"))
    assert [leg.amount for leg in legs] == [
        D("-17550.56"),
        D("100"),
        D("17550.56"),
        D("-100"),
    ]
    # str and int intake converted exactly via Decimal(value)
    assert legs[0].amount == D("-17550.56")


def test_transaction_is_none_before_exit(accounts, usd):
    with TransactionBuilder(account=accounts["cash"], timestamp=TS) as b:
        assert b.transaction is None
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="1.00")
    assert b.transaction is not None


def test_float_amount_rejected_before_quantization(accounts, usd):
    """PADR-0006 Rule 3 — floats are the host's wire convention; reject loudly."""
    with (
        pytest.raises(TypeError, match="Decimal"),  # the remedy is named
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount=1.1)  # float-ok
    assert Transaction.objects.count() == 0


def test_excess_precision_raises_and_persists_nothing(accounts, usd):
    """[D-5] silent truncation is forbidden: USD is 2dp, 1.234 must raise."""
    with (
        pytest.raises(ExcessPrecisionError),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1.234")
        b.add_leg(account=accounts["external"], instrument=usd, amount="1.234")
    assert Transaction.objects.count() == 0
    assert TransactionLeg.objects.count() == 0


def test_same_owner_invariant(accounts, usd):
    """[D-3]: every leg account must share transaction.account.owner."""
    intruder = get_user_model().objects.create_user(username="intruder", password="x")
    foreign = Account.objects.create(owner=intruder, name="foreign")
    with (
        pytest.raises(MixedOwnershipError, match="foreign"),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1.00")
        b.add_leg(account=foreign, instrument=usd, amount="1.00")
    assert Transaction.objects.count() == 0


def test_exception_in_block_persists_nothing(accounts, usd):
    with (
        pytest.raises(RuntimeError, match="abort"),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1.00")
        raise RuntimeError("abort")
    assert b.transaction is None
    assert Transaction.objects.count() == 0


@override_settings(DJANGO_ASSETS_USE_DB_TRIGGERS=False)
def test_python_fallback_raises_unbalanced_pre_commit(accounts, usd):
    """ADR-0004 matrix, triggers off: the builder is the integrity gate."""
    with (
        pytest.raises(UnbalancedTransactionError, match="USD"),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-2.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="1.00")
    assert Transaction.objects.count() == 0


@override_settings(DJANGO_ASSETS_USE_DB_TRIGGERS=False)
def test_python_fallback_checks_per_instrument(accounts, usd, eur):
    """Zero total across instruments is still unbalanced per instrument."""
    with (
        pytest.raises(UnbalancedTransactionError),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-1.00")
        b.add_leg(account=accounts["eur_cash"], instrument=eur, amount="1.00")
    assert Transaction.objects.count() == 0


def test_trigger_mode_unbalanced_surfaces_integrity_error(accounts, usd):
    """With triggers on, the failure shape is IntegrityError at COMMIT [D-9]."""
    with (
        pytest.raises(IntegrityError, match="Unbalanced transaction"),
        transaction.atomic(),
        TransactionBuilder(account=accounts["cash"], timestamp=TS) as b,
    ):
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-2.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="1.00")


@pytest.mark.parametrize(
    ("code", "decimals", "amount"),
    [
        ("USD", 2, "17550.56"),
        ("JPY", 0, "2600000"),
        ("BTC", 8, "0.00012345"),
        ("ETH", 18, "0.000000000000000001"),
        ("SPY260618C600", 4, "3.0000"),
    ],
)
def test_build_then_read_back_equals_input(accounts, code, decimals, amount):
    """ADR-0013 uniformity: one storage path for every unit of value."""
    from django_assets.core.models import Instrument

    inst = Instrument.objects.create(code=code, quantity_decimals=decimals)
    with TransactionBuilder(account=accounts["cash"], timestamp=TS) as b:
        b.add_leg(account=accounts["cash"], instrument=inst, amount=amount)
        b.add_leg(account=accounts["external"], instrument=inst, amount=f"-{amount}")
    legs = {leg.account_id: leg.amount for leg in b.transaction.legs.all()}
    assert legs[accounts["cash"].pk] == D(amount)
    assert legs[accounts["external"].pk] == -D(amount)
