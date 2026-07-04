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
from django_assets.core.queries import SupportsGetPrice
from django_assets.trades.exceptions import OverAllocationError


class _PnlEvent:
    """Per-event P&L walk state: position deltas + attributed cash."""

    def __init__(self) -> None:
        self.positions: dict[Instrument, Decimal] = {}
        self.cash: Decimal = Decimal(0)
        self.sort_key: tuple = (None, 0, 0)  # type: ignore[type-arg]


class TradeQuerySet(models.QuerySet["Trade"]):
    """DB-level query surface (spec §5); documented as potentially
    expensive at the .open()/.closed() aggregation — acceptable at
    retail scale (ADR-0016 philosophy). Queryset-level status uses each
    trade's OWN allocations; instance properties aggregate descendants."""

    def for_user(self, user: object) -> "TradeQuerySet":
        from typing import cast

        return self.filter(user=cast("int", user))

    def root_trades(self) -> "TradeQuerySet":
        return self.filter(parent__isnull=True)

    def children_of(self, trade: "Trade") -> "TradeQuerySet":
        return self.filter(parent=trade)

    def descendants_of(self, trade: "Trade") -> "TradeQuerySet":
        return self.filter(pk__in=[pk for pk in trade._tree_pks() if pk != trade.pk])

    def ancestors_of(self, trade: "Trade") -> "TradeQuerySet":
        pks, ancestor = [], trade.parent
        while ancestor is not None:
            pks.append(ancestor.pk)
            ancestor = ancestor.parent
        return self.filter(pk__in=pks)

    def open(self) -> "TradeQuerySet":
        open_ids = (
            TradeAllocation.objects.filter(category="")
            .values("trade_id", "leg__instrument_id")
            .annotate(total=models.Sum("amount"))
            .exclude(total=0)
            .values_list("trade_id", flat=True)
        )
        return self.filter(pk__in=open_ids)

    def closed(self) -> "TradeQuerySet":
        return self.exclude(pk__in=self.open().values_list("pk", flat=True))

    def with_instrument(self, instrument: Instrument) -> "TradeQuerySet":
        return self.filter(allocations__leg__instrument=instrument).distinct()

    def with_tag(self, category_code: str, tag_name: str) -> "TradeQuerySet":
        return self.filter(tags__category__code=category_code, tags__name=tag_name).distinct()

    def with_category(self, category_code: str) -> "TradeQuerySet":
        return self.filter(tags__category__code=category_code).distinct()

    def with_tags(self, **categories: "str | list[str]") -> "TradeQuerySet":
        """AND across categories, OR within a category's names."""
        qs = self
        for code, names in categories.items():
            values = [names] if isinstance(names, str) else list(names)
            qs = qs.filter(tags__category__code=code, tags__name__in=values)
        return qs.distinct()

    def with_tags_any(self, category_code: str, names: "list[str]") -> "TradeQuerySet":
        return self.filter(tags__category__code=category_code, tags__name__in=names).distinct()

    def with_tags_all(self, category_code: str, names: "list[str]") -> "TradeQuerySet":
        qs = self
        for name in names:
            qs = qs.filter(tags__category__code=category_code, tags__name=name)
        return qs.distinct()


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
    tags: "models.ManyToManyField[Tag, models.Model]" = models.ManyToManyField(
        "Tag", related_name="trades", blank=True
    )

    objects: ClassVar[TradeQuerySet] = TradeQuerySet.as_manager()  # type: ignore[assignment]

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

        Leg roles come from double-entry structure: mirrored same-
        instrument pairs split into a user-side leg and a counterparty
        mirror (never allocated). Cash-role instruments are those with
        no price_currency (base currencies). The largest non-mirror cash
        leg is the settlement slice (revenue/cost by sign); smaller cash
        legs are fees. quantity= targets one instrument's position;
        fraction= takes that share of EVERY user-side leg. An assign
        that exhausts a leg takes exact remainders (no rounding residue).
        """
        # id order = template insertion order: every template adds the
        # user-side leg of a mirrored pair first, which keeps the
        # user/mirror tiebreak deterministic when structure alone is
        # symmetric (pure two-leg events like expiries).
        legs = list(transaction.legs.select_related("account", "instrument").order_by("id"))
        groups: dict[int, list[TransactionLeg]] = {}
        for leg in legs:
            groups.setdefault(leg.instrument_id, []).append(leg)

        mirror_accounts: set[int] = set()
        position_legs: dict[int, TransactionLeg] = {}
        for group in groups.values():
            if len(group) != 2:
                continue
            user_leg, mirrors = _split_position_and_mirror(group, legs)
            mirror_accounts |= mirrors
            if user_leg.instrument.price_currency_id is not None:
                position_legs[user_leg.instrument_id] = user_leg

        if fraction is not None:
            ratio = to_decimal(fraction, param="fraction")
            targets = list(position_legs.values())
            exhausts = ratio == 1
        elif quantity is not None and instrument is not None:
            target = position_legs.get(instrument.pk)
            if target is None:
                raise ValueError(
                    f"transaction {transaction.pk} has no mirrored position "
                    f"legs of {instrument.code}"
                )
            qty = to_decimal(quantity, param="quantity")
            already = TradeAllocation.objects.filter(leg=target).aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal(0)
            exhausts = qty == abs(target.amount) - abs(already)
            ratio = qty / abs(target.amount)
            targets = [target]
        else:
            raise ValueError("assign() needs quantity= plus instrument=, or fraction=")

        allocations: list[TradeAllocation] = []
        with db_transaction.atomic():
            for leg in targets:
                allocations.append(self.assign_leg(leg, _slice(leg, ratio, exhausts)))
            cash_legs = sorted(
                (
                    leg
                    for leg in legs
                    if leg.account_id not in mirror_accounts
                    and leg.instrument.price_currency_id is None
                ),
                key=lambda leg: abs(leg.amount),
                reverse=True,
            )
            for index, leg in enumerate(cash_legs):
                slice_amount = _slice(leg, ratio, exhausts)
                if slice_amount == 0:
                    continue
                settlement = "revenue" if leg.amount > 0 else "cost"
                category = settlement if index == 0 else "fee"
                allocations.append(self.assign_leg(leg, slice_amount, category=category))
        return allocations

    # -- derived views (never stored; real + virtual layers) ---------------

    def net_position(self, instrument: Instrument | None = None) -> Decimal:
        tree = self._tree_pks()
        allocations = TradeAllocation.objects.filter(trade_id__in=tree, category="")
        entries = VirtualEntry.objects.filter(trade_id__in=tree, category="")
        if instrument is not None:
            allocations = allocations.filter(leg__instrument=instrument)
            entries = entries.filter(instrument=instrument)
        real = allocations.aggregate(total=models.Sum("amount"))["total"] or Decimal(0)
        virtual = entries.aggregate(total=models.Sum("amount"))["total"] or Decimal(0)
        return real + virtual

    def tracked_instruments(self) -> "list[Instrument]":
        """Instruments with position (category='') activity — cash
        settlement roles carry categories and are excluded by design."""
        tree = self._tree_pks()
        ids = set(
            TradeAllocation.objects.filter(trade_id__in=tree, category="")
            .values_list("leg__instrument_id", flat=True)
            .distinct()
        ) | set(
            VirtualEntry.objects.filter(trade_id__in=tree, category="")
            .values_list("instrument_id", flat=True)
            .distinct()
        )
        return list(Instrument.objects.filter(pk__in=ids).order_by("pk"))

    def _position_events(
        self, instruments: "list[Instrument] | None" = None
    ) -> "list[tuple[datetime.datetime, Decimal, int]]":
        """Merged (timestamp, amount, instrument) stream: real legs first
        at equal timestamps, then virtual entries, tiebroken by id."""
        tree = self._tree_pks()
        allocations = TradeAllocation.objects.filter(trade_id__in=tree)
        entries = VirtualEntry.objects.filter(trade_id__in=tree)
        if instruments is None:
            allocations = allocations.filter(category="")
            entries = entries.filter(category="")
        else:
            pks = [inst.pk for inst in instruments]
            allocations = allocations.filter(leg__instrument_id__in=pks)
            entries = entries.filter(instrument_id__in=pks)
        merged = [
            (ts, 0, row_id, amount, instrument_id)
            for ts, amount, instrument_id, row_id in allocations.values_list(
                "leg__transaction__timestamp", "amount", "leg__instrument_id", "pk"
            )
        ] + [
            (ts, 1, row_id, amount, instrument_id)
            for ts, amount, instrument_id, row_id in entries.values_list(
                "transfer__timestamp", "amount", "instrument_id", "pk"
            )
        ]
        merged.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(ts, amount, instrument_id) for ts, _k, _i, amount, instrument_id in merged]

    def status_for(self, instruments: "list[Instrument] | None" = None) -> str:
        events = self._position_events(instruments)
        positions: dict[int, Decimal] = {}
        for _ts, amount, instrument_id in events:
            positions[instrument_id] = positions.get(instrument_id, Decimal(0)) + amount
        open_now = any(total != 0 for total in positions.values())
        return "open" if open_now else "closed"

    @property
    def status(self) -> str:
        return self.status_for()

    @property
    def open_date(self) -> datetime.datetime | None:
        """Earliest event where a tracked position left zero — real leg
        or virtual entry alike."""
        positions: dict[int, Decimal] = {}
        for ts, amount, instrument_id in self._position_events():
            before = positions.get(instrument_id, Decimal(0))
            positions[instrument_id] = before + amount
            if before == 0 and positions[instrument_id] != 0:
                return ts
        return None

    @property
    def closed_date(self) -> datetime.datetime | None:
        """Latest event where ALL tracked positions returned to zero;
        None while open. A trade can close purely virtually."""
        positions: dict[int, Decimal] = {}
        result: datetime.datetime | None = None
        events = self._position_events()
        for ts, amount, instrument_id in events:
            positions[instrument_id] = positions.get(instrument_id, Decimal(0)) + amount
            result = ts if all(total == 0 for total in positions.values()) else None
        return result if events else None

    def check_consistency(self) -> "dict[str, list[str]]":
        """Re-run the checks over the trade's history (spec §5):
        partition violations are ERRORS (ADR-0030); position crossings —
        including retroactive ones from later real-allocation edits —
        are WARNINGS, never blocked (ADR-0031/0022)."""
        errors: list[str] = []
        warnings: list[str] = []
        tree = self._tree_pks()
        leg_ids = (
            TradeAllocation.objects.filter(trade_id__in=tree)
            .values_list("leg_id", flat=True)
            .distinct()
        )
        for leg in TransactionLeg.objects.filter(pk__in=leg_ids):
            allocated = TradeAllocation.objects.filter(leg=leg).aggregate(
                total=models.Sum("amount")
            )["total"] or Decimal(0)
            if abs(allocated) > abs(leg.amount):
                errors.append(
                    f"partition violation on leg {leg.pk}: allocations sum to "
                    f"{allocated} against leg amount {leg.amount}"
                )
        # Crossing walk over the merged event stream, virtual rows only.
        events = self._merged_events_with_kind()
        positions: dict[int, Decimal] = {}
        for _ts, kind, _row, amount, instrument_id in events:
            before = positions.get(instrument_id, Decimal(0))
            after = before + amount
            if (
                kind == 1
                and before != 0
                and (amount > 0) != (before > 0)
                and abs(amount) > abs(before)
            ):
                warnings.append(
                    f"position crossing: virtual entry of {amount} on instrument "
                    f"{instrument_id} pushed the book from {before} through zero"
                )
            positions[instrument_id] = after
        # Retroactive check: a non-zero position resting purely on
        # virtual entries means a later real-allocation edit pulled the
        # book out from under a transfer (warn, never block).
        real_net: dict[int, Decimal] = {}
        virtual_net: dict[int, Decimal] = {}
        for _ts, kind, _row, amount, instrument_id in events:
            bucket = real_net if kind == 0 else virtual_net
            bucket[instrument_id] = bucket.get(instrument_id, Decimal(0)) + amount
        for instrument_id, virtual_total in virtual_net.items():
            if virtual_total != 0 and real_net.get(instrument_id, Decimal(0)) == 0:
                warnings.append(
                    f"virtual-only position of {virtual_total} in instrument "
                    f"{instrument_id}: a real-allocation edit retroactively "
                    f"created a position crossing (ADR-0031 — warned, never blocked)"
                )
        return {"errors": errors, "warnings": warnings}

    def _merged_events_with_kind(
        self,
    ) -> "list[tuple[datetime.datetime, int, int, Decimal, int]]":
        tree = self._tree_pks()
        merged = [
            (ts, 0, row_id, amount, instrument_id)
            for ts, amount, instrument_id, row_id in TradeAllocation.objects.filter(
                trade_id__in=tree, category=""
            ).values_list("leg__transaction__timestamp", "amount", "leg__instrument_id", "pk")
        ] + [
            (ts, 1, row_id, amount, instrument_id)
            for ts, amount, instrument_id, row_id in VirtualEntry.objects.filter(
                trade_id__in=tree, category=""
            ).values_list("transfer__timestamp", "amount", "instrument_id", "pk")
        ]
        merged.sort(key=lambda item: (item[0], item[1], item[2]))
        return merged

    # -- tagging (spec §2.5) ----------------------------------------------

    def add_tag(self, category_code: str, tag_name: str) -> "Tag":
        """Get-or-create within THIS user's vocabulary, then attach."""
        category, _ = TagCategory.objects.get_or_create(
            user=self.user, code=category_code, defaults={"name": category_code}
        )
        tag, _ = Tag.objects.get_or_create(category=category, name=tag_name)
        self.tags.add(tag)
        return tag

    def remove_tag(self, category_code: str, tag_name: str) -> None:
        self.tags.remove(*self.tags.filter(category__code=category_code, name=tag_name))

    def get_tags_by_category(self) -> "dict[str, list[str]]":
        result: dict[str, list[str]] = {}
        for tag in self.tags.select_related("category").order_by("category__code", "name"):
            result.setdefault(tag.category.code, []).append(tag.name)
        return result

    # -- P&L (spec §4; average-cost event walk, never stored) --------------

    def calculate_pnl(
        self,
        as_of: datetime.datetime | None = None,
        price_source: "SupportsGetPrice | None" = None,
    ) -> "dict[str, object]":
        """Realized from revenue/cost cash slices by average-cost walk;
        fee-category slices are already inside our allocator's net cash
        slices and are NOT re-subtracted (reported via get_summary).
        Unrealized marks open positions via a PriceSource; None without
        one — unpriced positions are surfaced, never zeroed."""
        allocations = TradeAllocation.objects.filter(trade_id__in=self._tree_pks()).select_related(
            "leg", "leg__transaction", "leg__instrument"
        )
        if as_of is not None:
            allocations = allocations.filter(leg__transaction__timestamp__lte=as_of)

        events: dict[tuple[int, int], _PnlEvent] = {}
        for allocation in allocations.order_by(
            "leg__transaction__timestamp", "leg__transaction_id"
        ):
            key = (0, allocation.leg.transaction_id)
            event = events.setdefault(key, _PnlEvent())
            event.sort_key = (allocation.leg.transaction.timestamp, 0, key[1])
            if allocation.category == "":
                inst = allocation.leg.instrument
                event.positions[inst] = event.positions.get(inst, Decimal(0)) + allocation.amount
            elif allocation.category in ("revenue", "cost"):
                event.cash += allocation.amount
        virtual_entries = VirtualEntry.objects.filter(trade_id__in=self._tree_pks()).select_related(
            "transfer", "instrument"
        )
        if as_of is not None:
            virtual_entries = virtual_entries.filter(transfer__timestamp__lte=as_of)
        for entry in virtual_entries:
            key = (1, entry.transfer_id)
            event = events.setdefault(key, _PnlEvent())
            event.sort_key = (entry.transfer.timestamp, 1, key[1])
            if entry.category == "":
                event.positions[entry.instrument] = (
                    event.positions.get(entry.instrument, Decimal(0)) + entry.amount
                )
            elif entry.category in ("revenue", "cost"):
                event.cash += entry.amount

        realized = Decimal(0)
        position: dict[Instrument, Decimal] = {}
        basis: dict[Instrument, Decimal] = {}
        for event in sorted(events.values(), key=lambda item: item.sort_key):
            # Within an event, cash follows the OPENING side when both
            # openings and closings occur (assignment/exercise: the strike
            # cash IS the new position's basis); otherwise it attaches to
            # whichever side exists, split equally across instruments.
            openings: list[tuple[Instrument, Decimal]] = []
            closings: list[tuple[Instrument, Decimal]] = []
            for inst, delta in event.positions.items():
                pos = position.get(inst, Decimal(0))
                if pos == 0 or (delta > 0) == (pos > 0):
                    openings.append((inst, delta))
                else:
                    closings.append((inst, delta))
            cash_targets = openings if openings else closings
            share = event.cash / len(cash_targets) if cash_targets else Decimal(0)
            for inst, delta in closings:
                pos = position.get(inst, Decimal(0))
                held_basis = basis.get(inst, Decimal(0))
                cash = Decimal(0) if openings else share
                closing = min(abs(delta), abs(pos))
                released = held_basis * closing / abs(pos)
                realized += cash * closing / abs(delta) + released
                basis[inst] = held_basis - released
                position[inst] = pos + delta
                if abs(delta) > closing:  # crossed through zero
                    basis[inst] = basis.get(inst, Decimal(0)) + cash * (abs(delta) - closing) / abs(
                        delta
                    )
            for inst, delta in openings:
                position[inst] = position.get(inst, Decimal(0)) + delta
                basis[inst] = basis.get(inst, Decimal(0)) + share
            if not event.positions and event.cash:
                realized += event.cash  # pure-cash event (virtual fee move)

        open_basis = sum(basis[inst] for inst, pos in position.items() if pos != 0)
        cost_basis = -open_basis
        current_value: Decimal | None = None
        unpriced: list[Instrument] = []
        if price_source is not None:
            from django_assets.core.measure import value as measure_value

            current_value = Decimal(0)
            for inst, pos in position.items():
                if pos == 0:
                    continue
                quote = price_source.get_price(inst, at=as_of)
                if quote is None:
                    unpriced.append(inst)
                    continue
                current_value += measure_value(pos, quote.price, inst).amount
        else:
            unpriced = [inst for inst, pos in position.items() if pos != 0]

        unrealized = current_value - cost_basis if current_value is not None else None
        total = realized + (unrealized if unrealized is not None else Decimal(0))
        return {
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": total,
            "cost_basis": cost_basis,
            "current_value": current_value,
            "transactions_count": sum(1 for key in events if key[0] == 0),
            "unpriced": unpriced,
        }

    def get_summary(self, as_of: datetime.datetime | None = None) -> "dict[str, object]":
        fees = TradeAllocation.objects.filter(trade_id__in=self._tree_pks(), category="fee")
        if as_of is not None:
            fees = fees.filter(leg__transaction__timestamp__lte=as_of)
        return {
            **self.calculate_pnl(as_of=as_of),
            "fees": fees.aggregate(total=models.Sum("amount"))["total"] or Decimal(0),
            "status": self.status,
            "tags": self.get_tags_by_category(),
        }

    def accounts_involved(self) -> "list[int]":
        return list(
            TradeAllocation.objects.filter(trade_id__in=self._tree_pks())
            .values_list("leg__account_id", flat=True)
            .distinct()
        )


def _slice(leg: TransactionLeg, ratio: Decimal, exhausts: bool) -> Decimal:
    """Pro-rata slice of a leg: quantized per the leg instrument, except
    a final (exhausting) assign takes the exact remainder."""
    if exhausts:
        taken = TradeAllocation.objects.filter(leg=leg).aggregate(total=models.Sum("amount"))[
            "total"
        ] or Decimal(0)
        return leg.amount - taken
    return leg.instrument.quantize(leg.amount * ratio)


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


class VirtualTransfer(models.Model):
    """The trades-book analog of a core Transaction (ADR-0031): an
    atomic, balanced event moving position and P&L BETWEEN trades. No FK
    to any core ledger row exists, by construction."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="virtual_transfers"
    )
    timestamp = models.DateTimeField(db_index=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["VirtualTransfer"]] = models.Manager()

    #: Advisory PositionCrossingWarning list, attached by the helpers —
    #: informational only, never persisted, never blocking (ADR-0031).
    warnings: "list[PositionCrossingWarning]"

    def __str__(self) -> str:
        return f"virtual transfer {self.pk} @ {self.timestamp:%Y-%m-%d}"


class VirtualEntry(models.Model):
    """One trade's side of a virtual transfer; per transfer, per
    instrument, entries sum to exactly zero (DB-enforced at COMMIT)."""

    transfer = models.ForeignKey(VirtualTransfer, on_delete=models.CASCADE, related_name="entries")
    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name="virtual_entries")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=40, decimal_places=18)
    category = models.CharField(max_length=30, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["VirtualEntry"]] = models.Manager()

    class Meta:
        indexes = [
            # The balance trigger's GROUP BY (spec §2.4).
            models.Index(fields=["transfer", "instrument"], name="virtualentry_balance_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.instrument.code} → {self.trade}"


class PositionCrossingWarning:
    """Advisory: an entry pushed a trade's book through zero (ADR-0031).
    The balance rule guarantees the excess landed in the counterparty
    trade(s); the aggregate still equals the ledger."""

    def __init__(self, trade: Trade, instrument: Instrument, kind: str, detail: str) -> None:
        self.trade = trade
        self.instrument = instrument
        self.kind = kind  # "position" | "cash"
        self.detail = detail

    def __repr__(self) -> str:
        return f"PositionCrossingWarning({self.kind}: {self.detail})"


class TagCategory(models.Model):
    """A user-defined tag dimension (ADR-0030 §5): strategy, conviction…"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tag_categories"
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["TagCategory"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "code"], name="uniq_tagcategory_user_code"),
        ]
        verbose_name_plural = "tag categories"

    def __str__(self) -> str:
        return self.code


class Tag(models.Model):
    """A value within one category; flat, user-scoped via the category."""

    category = models.ForeignKey(TagCategory, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["Tag"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["category", "name"], name="uniq_tag_category_name"),
        ]

    def __str__(self) -> str:
        return f"{self.category.code}:{self.name}"


def guard_same_user_tags(
    sender: object, instance: Trade, action: str, pk_set: "set[int] | None", **kwargs: object
) -> None:
    """m2m_changed: a tag may only attach to a trade of the same user."""
    if action != "pre_add" or not pk_set:
        return
    tag_ids = pk_set
    foreign = Tag.objects.filter(pk__in=tag_ids).exclude(category__user=instance.user_id)
    if foreign.exists():
        raise ValueError("tags attach only to trades of the same user (ADR-0030)")
