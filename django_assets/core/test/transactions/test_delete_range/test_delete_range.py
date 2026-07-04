"""C6: TransactionBuilder.delete_range — spec §4.3, ADR-0019.

Deletes an account's Transactions with timestamp in [from_, to_).
Refuses without confirm=True. Whole-transaction deletion keeps the
trigger satisfied.
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Transaction

pytestmark = pytest.mark.ledger

D = Decimal
T1 = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=datetime.UTC)
T2 = datetime.datetime(2026, 3, 11, 12, 0, tzinfo=datetime.UTC)
T3 = datetime.datetime(2026, 3, 12, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def three_txs(accounts, usd):
    for ts, desc in [(T1, "t1"), (T2, "t2"), (T3, "t3")]:
        with TransactionBuilder(account=accounts["cash"], timestamp=ts, description=desc) as b:
            b.add_leg(account=accounts["cash"], instrument=usd, amount="10.00")
            b.add_leg(account=accounts["external"], instrument=usd, amount="-10.00")
    return accounts


def test_refuses_without_confirm(three_txs):
    with pytest.raises(ValueError, match="confirm"):
        TransactionBuilder.delete_range(three_txs["cash"], T1, T3)
    assert Transaction.objects.count() == 3


def test_deletes_half_open_range(three_txs):
    """[from_, to_): T1 and T2 fall inside, T3 is excluded by the open end."""
    deleted = TransactionBuilder.delete_range(three_txs["cash"], T1, T3, confirm=True)
    assert deleted == 2
    remaining = list(Transaction.objects.values_list("description", flat=True))
    assert remaining == ["t3"]


def test_scoped_to_the_account(three_txs, accounts, usd, user):
    """Another account's transactions in the window are untouched."""
    other = Account.objects.create(owner=user, name="other")
    with TransactionBuilder(account=other, timestamp=T2, description="other-t2") as b:
        b.add_leg(account=other, instrument=usd, amount="5.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="-5.00")
    deleted = TransactionBuilder.delete_range(three_txs["cash"], T1, T3, confirm=True)
    assert deleted == 2
    assert Transaction.objects.filter(description="other-t2").exists()


def test_returns_zero_for_empty_window(three_txs):
    later = T3 + datetime.timedelta(days=30)
    assert (
        TransactionBuilder.delete_range(
            three_txs["cash"], later, later + datetime.timedelta(days=1), confirm=True
        )
        == 0
    )
