"""Trades models: Trade + TradeAllocation (trades spec §2, ADR-0030).

Trades are the user's interpretation layer: quantity-level fractional
slices of core legs, on trades' own books. Core rows are never written
(the Inviolability Rule); the partition rule is enforced app-level
(OverAllocationError) and DB-level (deferred trigger, §2.4).
"""

import datetime
from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
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

    def clean(self) -> None:
        """Same-user parenting; cycle prevention by walking ancestors."""
        if self.parent is None:
            return
        if self.parent.user_id != self.user_id:
            raise ValidationError("a trade's parent must belong to the same user")
        ancestor: Trade | None = self.parent
        while ancestor is not None:
            if ancestor.pk == self.pk:
                raise ValidationError("trade hierarchy cannot contain cycles")
            ancestor = ancestor.parent

    def _tree_pks(self) -> list[int]:
        """This trade + all descendants (derived views aggregate them)."""
        pks, frontier = [self.pk], [self.pk]
        while frontier:
            frontier = list(
                Trade.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
            )
            pks.extend(frontier)
        return pks

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

    def assign(
        self,
        transaction: Transaction,
        *,
        quantity: Decimal | int | str | None = None,
        instrument: Instrument | None = None,
        fraction: Decimal | int | str | None = None,
    ) -> "list[TradeAllocation]":
        """The default pro-rata allocator (spec §4, ADR-0030 §3).

        Leg-role convention, grounded in double-entry: the accounts
        holding the OPPOSITE-sign asset leg are the counterparty mirror —
        never allocated. The perspective account's cash slice is
        revenue (+) / cost (−); other cash legs are fees. Cash slices
        quantize to each leg's instrument precision; an assign that
        exhausts the asset leg takes exact remainders so slices always
        sum to the full leg (no rounding residue).
        """
        legs = list(transaction.legs.select_related("account", "instrument"))
        asset_legs = [
            leg for leg in legs if instrument is None or leg.instrument_id == instrument.pk
        ]
        if instrument is None:
            # fraction mode: the tracked asset is the non-cash instrument
            # with mirrored legs; fall back to the largest instrument group.
            by_instrument: dict[int, list[TransactionLeg]] = {}
            for leg in legs:
                by_instrument.setdefault(leg.instrument_id, []).append(leg)
            candidates = [group for group in by_instrument.values() if len(group) == 2]
            asset_legs = max(candidates, key=lambda group: abs(group[0].amount))
        position_leg, mirror_accounts = _split_position_and_mirror(asset_legs, legs)

        if fraction is not None:
            ratio = to_decimal(fraction, param="fraction")
            qty = abs(position_leg.amount) * ratio
        elif quantity is not None:
            qty = to_decimal(quantity, param="quantity")
        else:
            raise ValueError("assign() needs quantity= or fraction=")
        already = TradeAllocation.objects.filter(leg=position_leg).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal(0)
        remaining = abs(position_leg.amount) - abs(already)
        exhausts = qty == remaining
        ratio = qty / abs(position_leg.amount)

        allocations: list[TradeAllocation] = []
        with db_transaction.atomic():
            sign = 1 if position_leg.amount > 0 else -1
            allocations.append(self.assign_leg(position_leg, sign * qty))
            for leg in legs:
                if leg.pk == position_leg.pk or leg.account_id in mirror_accounts:
                    continue
                if leg.instrument_id == position_leg.instrument_id:
                    continue  # other asset slices only via explicit assign_leg
                if exhausts:
                    taken = TradeAllocation.objects.filter(leg=leg).aggregate(
                        total=models.Sum("amount")
                    )["total"] or Decimal(0)
                    slice_amount = leg.amount - taken
                else:
                    slice_amount = leg.instrument.quantize(leg.amount * ratio)
                if slice_amount == 0:
                    continue
                if leg.account_id == transaction.account_id:
                    category = "revenue" if leg.amount > 0 else "cost"
                else:
                    category = "fee"
                allocations.append(self.assign_leg(leg, slice_amount, category=category))
        return allocations

    # -- derived views (never stored) -------------------------------------

    def net_position(self, instrument: Instrument | None = None) -> Decimal:
        allocations = TradeAllocation.objects.filter(trade_id__in=self._tree_pks(), category="")
        if instrument is not None:
            allocations = allocations.filter(leg__instrument=instrument)
        total = allocations.aggregate(total=models.Sum("amount"))["total"]
        return total if total is not None else Decimal(0)

    def tracked_instruments(self) -> "list[Instrument]":
        """Instruments with position (category='') allocations — cash
        settlement roles carry categories and are excluded by design."""
        ids = (
            TradeAllocation.objects.filter(trade_id__in=self._tree_pks(), category="")
            .values_list("leg__instrument_id", flat=True)
            .distinct()
        )
        return list(Instrument.objects.filter(pk__in=ids).order_by("pk"))

    def _position_events(
        self, instruments: "list[Instrument] | None" = None
    ) -> "list[tuple[datetime.datetime, Decimal, int]]":
        allocations = TradeAllocation.objects.filter(trade_id__in=self._tree_pks())
        if instruments is None:
            allocations = allocations.filter(category="")
        else:
            allocations = allocations.filter(leg__instrument__in=[inst.pk for inst in instruments])
        return list(
            allocations.values_list(
                "leg__transaction__timestamp", "amount", "leg__instrument_id"
            ).order_by("leg__transaction__timestamp", "leg__transaction_id")
        )

    def status_for(self, instruments: "list[Instrument] | None" = None) -> str:
        events = self._position_events(instruments)
        positions: dict[int, Decimal] = {}
        for _ts, amount, instrument_id in events:
            positions[instrument_id] = positions.get(instrument_id, Decimal(0)) + amount
        open_now = any(total != 0 for total in positions.values())
        return "open" if open_now else ("closed" if events else "closed")

    @property
    def status(self) -> str:
        return self.status_for()

    @property
    def open_date(self) -> datetime.datetime | None:
        """Earliest settlement timestamp where a tracked position left 0."""
        positions: dict[int, Decimal] = {}
        for ts, amount, instrument_id in self._position_events():
            before = positions.get(instrument_id, Decimal(0))
            positions[instrument_id] = before + amount
            if before == 0 and positions[instrument_id] != 0:
                return ts
        return None

    @property
    def closed_date(self) -> datetime.datetime | None:
        """Latest timestamp where ALL tracked positions returned to 0;
        None while open."""
        positions: dict[int, Decimal] = {}
        result: datetime.datetime | None = None
        events = self._position_events()
        for ts, amount, instrument_id in events:
            positions[instrument_id] = positions.get(instrument_id, Decimal(0)) + amount
            result = ts if all(total == 0 for total in positions.values()) else None
        return result if events else None

    def accounts_involved(self) -> "list[int]":
        return list(
            TradeAllocation.objects.filter(trade_id__in=self._tree_pks())
            .values_list("leg__account_id", flat=True)
            .distinct()
        )


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


def _split_position_and_mirror(
    asset_legs: "list[TransactionLeg]", all_legs: "list[TransactionLeg]"
) -> "tuple[TransactionLeg, set[int]]":
    """The double-entry role heuristic: with a mirrored asset pair, the
    counterparty account is the one whose CASH legs oppose its asset leg
    less coherently — concretely, the user side has asset and perspective
    cash on different accounts, while the mirror account carries both
    opposite-sign entries. We pick the position leg as the one whose
    account does NOT also hold an opposite-sign cash leg of the
    transaction; its mirror's account(s) are excluded from allocation."""
    if len(asset_legs) == 1:
        return asset_legs[0], set()
    first, second = asset_legs[0], asset_legs[1]
    cash_by_account: dict[int, Decimal] = {}
    for leg in all_legs:
        if leg.instrument_id != first.instrument_id:
            cash_by_account[leg.account_id] = (
                cash_by_account.get(leg.account_id, Decimal(0)) + leg.amount
            )

    def is_mirror(candidate: TransactionLeg) -> bool:
        cash = cash_by_account.get(candidate.account_id)
        return cash is not None and (cash > 0) != (candidate.amount > 0)

    # The mirror account holds asset and cash of OPPOSITE signs (it
    # received what it gave); the user side holds same-signed... the
    # reverse. Prefer the leg whose account holds no cash at all
    # (a pure holdings account), else the non-mirror.
    for candidate, other in ((first, second), (second, first)):
        if candidate.account_id not in cash_by_account:
            return candidate, {other.account_id}
    for candidate, other in ((first, second), (second, first)):
        if not is_mirror(candidate):
            return candidate, {other.account_id}
    return first, {second.account_id}
