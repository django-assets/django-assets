"""The Inviolability harness (trades spec §8): zero core-table writes
across any trades API call. Reused by every trades milestone."""

import contextlib
from collections.abc import Iterator

from django_assets.core.models import Transaction, TransactionLeg


def _core_state() -> tuple[list[tuple], list[tuple]]:
    transactions = list(
        Transaction.objects.order_by("pk").values_list(
            "pk", "account_id", "timestamp", "origin", "description"
        )
    )
    legs = list(
        TransactionLeg.objects.order_by("pk").values_list(
            "pk", "transaction_id", "account_id", "instrument_id", "amount"
        )
    )
    return transactions, legs


@contextlib.contextmanager
def inviolable() -> Iterator[None]:
    """assert: the wrapped trades operation writes nothing to core."""
    before = _core_state()
    yield
    after = _core_state()
    assert after == before, "Inviolability Rule violated: core tables changed"
