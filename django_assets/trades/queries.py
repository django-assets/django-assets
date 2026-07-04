"""Trades query helpers (spec §5): composable tag filters, the
unorganized-activity query, and transaction-side helpers (no core
patching)."""

from decimal import Decimal

from django.db.models import DecimalField, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from django_assets.core.models import Account, Transaction, TransactionLeg
from django_assets.trades.models import Trade


class TagFilter:
    """Composable Q-object wrapper: TagFilter(a, b) | TagFilter(c, d)."""

    def __init__(self, category_code: str, tag_name: str) -> None:
        self.q = Q(tags__category__code=category_code, tags__name=tag_name)

    def __or__(self, other: "TagFilter") -> "TagFilter":
        combined = TagFilter.__new__(TagFilter)
        combined.q = self.q | other.q
        return combined

    def __and__(self, other: "TagFilter") -> "TagFilter":
        combined = TagFilter.__new__(TagFilter)
        combined.q = self.q & other.q
        return combined


def unallocated_legs(account: Account | None = None) -> "QuerySet[TransactionLeg]":
    """Legs with remaining unallocated quantity — 'not yet organized'
    activity (ADR-0030 consequence)."""
    legs = TransactionLeg.objects.all()
    if account is not None:
        legs = legs.filter(account=account)
    zero = Value(Decimal(0), output_field=DecimalField(max_digits=40, decimal_places=18))
    return legs.annotate(allocated=Coalesce(Sum("trade_allocations__amount"), zero)).exclude(
        allocated=F("amount")
    )


def transactions_for(trade: Trade) -> "QuerySet[Transaction]":
    return Transaction.objects.filter(
        legs__trade_allocations__trade_id__in=trade._tree_pks()
    ).distinct()


def transactions_in_trades(trades: "QuerySet[Trade]") -> "QuerySet[Transaction]":
    return Transaction.objects.filter(legs__trade_allocations__trade__in=trades).distinct()
