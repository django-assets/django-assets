"""The balance trigger: per-instrument zero-sum at COMMIT (ADR-0004/0020).

Every test here uses @pytest.mark.ledger — deferred constraints only fire
on real COMMIT (PADR-0003).
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from django_assets.core.models import Transaction, TransactionLeg

pytestmark = pytest.mark.ledger

D = Decimal


def leg(tx, account, instrument, amount):
    return TransactionLeg.objects.create(
        transaction=tx, account=account, instrument=instrument, amount=D(amount)
    )


def test_balanced_multileg_buy_commits(make_tx, accounts, usd, aapl):
    """The ADR-0024 worked example: one CSV row, five legs, one commit."""
    with transaction.atomic():
        tx = make_tx("BUY 100 AAPL @ 175.50")
        leg(tx, accounts["cash"], usd, "-17550.56")
        leg(tx, accounts["holdings"], aapl, "100")
        leg(tx, accounts["external"], usd, "17550.00")
        leg(tx, accounts["commissions"], usd, "0.50")
        leg(tx, accounts["fees"], usd, "0.06")
        leg(tx, accounts["external"], aapl, "-100")
    assert TransactionLeg.objects.count() == 6


def test_unbalanced_insert_raises_at_commit_not_statement(make_tx, accounts, aapl):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            tx = make_tx()
            leg(tx, accounts["holdings"], aapl, "100")
            # Deferred: the constraint has NOT fired yet at statement time.
            assert TransactionLeg.objects.filter(transaction=tx).count() == 1
    assert TransactionLeg.objects.count() == 0


def test_update_that_unbalances_raises(make_tx, accounts, usd):
    with transaction.atomic():
        tx = make_tx()
        a = leg(tx, accounts["cash"], usd, "-100.00")
        leg(tx, accounts["external"], usd, "100.00")
    with pytest.raises(IntegrityError), transaction.atomic():
        a.amount = D("-90.00")
        a.save()


def test_offsetting_updates_in_one_commit_pass(make_tx, accounts, usd):
    with transaction.atomic():
        tx = make_tx()
        a = leg(tx, accounts["cash"], usd, "-100.00")
        b = leg(tx, accounts["external"], usd, "100.00")
    with transaction.atomic():
        a.amount = D("-150.00")
        a.save()
        b.amount = D("150.00")
        b.save()
    a.refresh_from_db()
    assert a.amount == D("-150.00")


def test_deleting_single_leg_raises(make_tx, accounts, usd):
    """DELETE must consult OLD — the README's original sketch missed this (D-4)."""
    with transaction.atomic():
        tx = make_tx()
        a = leg(tx, accounts["cash"], usd, "-100.00")
        leg(tx, accounts["external"], usd, "100.00")
    with pytest.raises(IntegrityError), transaction.atomic():
        a.delete()


def test_whole_transaction_delete_passes(make_tx, accounts, usd):
    with transaction.atomic():
        tx = make_tx()
        leg(tx, accounts["cash"], usd, "-100.00")
        leg(tx, accounts["external"], usd, "100.00")
    with transaction.atomic():
        tx.delete()
    assert Transaction.objects.count() == 0
    assert TransactionLeg.objects.count() == 0


def test_fx_four_leg_balances_per_instrument(make_tx, accounts, usd, eur):
    """ADR-0013: cross-currency is explicit multi-leg; balance is per instrument."""
    with transaction.atomic():
        tx = make_tx("FX 100 EUR -> 110 USD")
        leg(tx, accounts["eur_cash"], eur, "-100.00")
        leg(tx, accounts["external"], eur, "100.00")
        leg(tx, accounts["cash"], usd, "110.00")
        leg(tx, accounts["external"], usd, "-110.00")
    assert TransactionLeg.objects.count() == 4


def test_cross_instrument_imbalance_rejected(make_tx, accounts, usd, eur):
    """-100 EUR vs +100 USD does NOT balance: sums are per instrument."""
    with pytest.raises(IntegrityError), transaction.atomic():
        tx = make_tx()
        leg(tx, accounts["eur_cash"], eur, "-100.00")
        leg(tx, accounts["cash"], usd, "100.00")


def test_dec18_domain_rejects_scale_beyond_18(make_tx, accounts, usd):
    """The domain CHECK errors instead of numeric(40,18)'s silent rounding."""
    with pytest.raises(IntegrityError), transaction.atomic():
        tx = make_tx()
        leg(tx, accounts["cash"], usd, "-0.0000000000000000001")  # scale 19
        leg(tx, accounts["external"], usd, "0.0000000000000000001")


def test_leg_account_protect_and_owner_cascade(make_tx, accounts, usd, user):
    from django.db.models import ProtectedError

    with transaction.atomic():
        tx = make_tx(account_name="cash")
        leg(tx, accounts["cash"], usd, "-100.00")
        leg(tx, accounts["external"], usd, "100.00")
    # Deleting one account that other transactions' legs reference: blocked.
    with pytest.raises(ProtectedError):
        accounts["external"].delete()
    # Deleting the owner collects every account, transaction, and leg: clean.
    with transaction.atomic():
        user.delete()
    assert Transaction.objects.count() == 0
    assert TransactionLeg.objects.count() == 0
