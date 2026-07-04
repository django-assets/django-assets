"""Trades models: Trade + TradeAllocation (trades spec §2, ADR-0030).

Trades are the user's interpretation layer: quantity-level fractional
slices of core legs, on trades' own books. Core rows are never written
(the Inviolability Rule); the partition rule is enforced app-level
(OverAllocationError) and DB-level (deferred trigger, §2.4).
"""

from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db import transaction as db_transaction

from django_assets.core.intake import to_decimal
from django_assets.core.models import Instrument, Transaction, TransactionLeg
from django_assets.trades.exceptions import OverAllocationError


class Trade(models.Model):
    """A user-scoped grouping of allocation slices (ADR-0030 §5)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trades"
    )
    name = models.CharField(max_length=200, db_index=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[models.Manager["Trade"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="uniq_trade_name_per_user"),
        ]

    def __str__(self) -> str:
        return self.name

    # -- mutation API (spec §5; partition rule enforced) -----------------

    def assign_leg(
        self,
        leg: TransactionLeg,
        amount: Decimal | int | str,
        category: str = "",
    ) -> "TradeAllocation":
        """Allocate one precise slice of a core leg to this trade."""
        value = to_decimal(amount)
        _precheck_partition(leg, value)
        return TradeAllocation.objects.create(trade=self, leg=leg, amount=value, category=category)

    def assign_transaction(self, transaction: Transaction) -> "list[TradeAllocation]":
        """Allocate 100% of every leg of the transaction."""
        with db_transaction.atomic():
            return [self.assign_leg(leg, leg.amount) for leg in transaction.legs.order_by("id")]

    def unassign(self, target: Transaction | TransactionLeg) -> int:
        allocations = self.allocations.all()
        if isinstance(target, Transaction):
            allocations = allocations.filter(leg__transaction=target)
        else:
            allocations = allocations.filter(leg=target)
        count, _ = allocations.delete()
        return count

    def reallocate(
        self,
        leg: TransactionLeg,
        amount: Decimal | int | str,
        category: str = "",
    ) -> "TradeAllocation":
        """Resize this trade's existing slice of the leg."""
        value = to_decimal(amount)
        allocation = self.allocations.get(leg=leg, category=category)
        _precheck_partition(leg, value, exclude_pk=allocation.pk)
        allocation.amount = value
        allocation.save(update_fields=["amount"])
        return allocation

    # -- derived views (never stored) -------------------------------------

    def net_position(self, instrument: Instrument | None = None) -> Decimal:
        allocations = TradeAllocation.objects.filter(trade=self, category="")
        if instrument is not None:
            allocations = allocations.filter(leg__instrument=instrument)
        total = allocations.aggregate(total=models.Sum("amount"))["total"]
        return total if total is not None else Decimal(0)


def _precheck_partition(
    leg: TransactionLeg,
    amount: Decimal,
    *,
    exclude_pk: int | None = None,
) -> None:
    """The app-level partition pre-check: same sign, Σ|amounts| ≤
    |leg.amount| across ALL trades and categories (clear error before
    the trigger's COMMIT-time backstop)."""
    if amount == 0 or (amount > 0) != (leg.amount > 0):
        raise OverAllocationError(
            f"allocation {amount} must share the sign of leg {leg.pk} "
            f"({leg.amount}) and be non-zero (ADR-0030 partition rule)"
        )
    existing = TradeAllocation.objects.filter(leg=leg)
    if exclude_pk is not None:
        existing = existing.exclude(pk=exclude_pk)
    allocated = existing.aggregate(total=models.Sum("amount"))["total"] or Decimal(0)
    if abs(allocated + amount) > abs(leg.amount):
        raise OverAllocationError(
            f"allocating {amount} would put leg {leg.pk} at "
            f"{allocated + amount} against its amount {leg.amount} — the "
            f"partition rule caps total allocations at the leg's amount"
        )


class TradeAllocation(models.Model):
    """A slice of one core leg belonging to one trade (ADR-0030 §1).

    Signed, same sign as the leg, in the leg's instrument units.
    category '' = asset/position slice; revenue/cost/fee for cash slices
    (open vocabulary). Realized profit is computed, never stored.
    """

    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name="allocations")
    leg = models.ForeignKey(
        TransactionLeg, on_delete=models.CASCADE, related_name="trade_allocations"
    )
    amount = models.DecimalField(max_digits=40, decimal_places=18)
    category = models.CharField(max_length=30, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["TradeAllocation"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["trade", "leg", "category"], name="uniq_allocation_trade_leg_category"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} of leg {self.leg_id} → {self.trade}"
