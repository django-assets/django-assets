"""Lots models (lots spec §3, ADR-0032): derived, rebuildable state.

Everything here is a deterministic cache over the ledger (+ linkage
records): rebuilt, never hand-edited. The lot_conservation trigger set
backstops direct writes between rebuilds.
"""

from typing import ClassVar

from django.db import models

from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg


class Lot(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="lots")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="lots")
    opened_by_leg = models.ForeignKey(
        TransactionLeg, on_delete=models.CASCADE, related_name="opened_lots"
    )
    acquired_at = models.DateTimeField()
    """trade_timestamp when present, else settlement timestamp (ADR-0012)."""

    quantity = models.DecimalField(max_digits=40, decimal_places=18)
    quantity_remaining = models.DecimalField(max_digits=40, decimal_places=18)
    cost_basis = models.DecimalField(max_digits=40, decimal_places=18)
    """US convention: commissions and fees capitalized. For short lots
    this holds the opening PROCEEDS (open-by-sale inverts roles)."""

    cost_basis_remaining = models.DecimalField(max_digits=40, decimal_places=18)
    direction = models.CharField(max_length=5, choices=[("long", "Long"), ("short", "Short")])
    rollover_linked = models.BooleanField(default=False)
    """True when the basis includes rolled option premium (ADR-0032 §3)."""

    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["Lot"]] = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=["account", "instrument", "acquired_at"], name="lot_scope_idx"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.direction} {self.quantity_remaining}/{self.quantity} "
            f"{self.instrument.code} @ {self.acquired_at:%Y-%m-%d}"
        )


class LotMatch(models.Model):
    """One disposal slice against one lot (FIFO in v1).

    Uniform accounting: basis_recovered is always the lot-basis share
    released (conservation-friendly); proceeds is the closing event's
    cash share; realized_gain = proceeds − basis for longs and
    basis − proceeds for shorts (proceeds-as-opening inversion).
    """

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="matches")
    closing_leg = models.ForeignKey(
        TransactionLeg, on_delete=models.CASCADE, related_name="lot_matches"
    )
    quantity = models.DecimalField(max_digits=40, decimal_places=18)
    proceeds = models.DecimalField(max_digits=40, decimal_places=18)
    basis_recovered = models.DecimalField(max_digits=40, decimal_places=18)
    realized_gain = models.DecimalField(max_digits=40, decimal_places=18)
    term = models.CharField(max_length=5, choices=[("short", "Short"), ("long", "Long")])
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["LotMatch"]] = models.Manager()

    def __str__(self) -> str:
        return f"match {self.quantity} of lot {self.lot_id} ({self.term})"


class LotEvent(models.Model):
    """Explicit corporate-action adjustment record (ADR-0032 §6):
    'why does this lot show 400 shares at $37.50 basis' is answerable
    from rows, not inference. Derived like everything else here."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=40)
    source_transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE)
    ratio = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    quantity_before = models.DecimalField(max_digits=40, decimal_places=18)
    quantity_after = models.DecimalField(max_digits=40, decimal_places=18)
    basis_before = models.DecimalField(max_digits=40, decimal_places=18)
    basis_after = models.DecimalField(max_digits=40, decimal_places=18)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["LotEvent"]] = models.Manager()

    def __str__(self) -> str:
        return f"{self.event_type} on lot {self.lot_id}"


class ExerciseLink(models.Model):
    """Option-roundtrip ↔ delivered-shares linkage (ADR-0032 §3).

    source='metadata' rows materialize from the rollover tag instruments'
    templates write; source='manual' rows come from the linkage API,
    survive rebuilds, and win conflicts [D-42]."""

    transaction = models.ForeignKey(
        Transaction, on_delete=models.CASCADE, related_name="exercise_links"
    )
    delivered_leg = models.ForeignKey(
        TransactionLeg, on_delete=models.CASCADE, related_name="exercise_links"
    )
    option_instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(
        max_length=10, choices=[("metadata", "Metadata"), ("manual", "Manual")]
    )
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["ExerciseLink"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "delivered_leg"], name="uniq_exercise_link"
            ),
        ]


class ConversionLink(models.Model):
    """Conversion carryover linkage (ADR-0032 §5): connects a conversion
    transaction's source and target asset legs. Carries no rate — ever
    [D-43]. Same two-source contract as ExerciseLink."""

    transaction = models.ForeignKey(
        Transaction, on_delete=models.CASCADE, related_name="conversion_links"
    )
    from_instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="+")
    to_instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="+")
    from_quantity = models.DecimalField(max_digits=40, decimal_places=18)
    to_quantity = models.DecimalField(max_digits=40, decimal_places=18)
    source = models.CharField(
        max_length=10, choices=[("metadata", "Metadata"), ("manual", "Manual")]
    )
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["ConversionLink"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["transaction"], name="uniq_conversion_link"),
        ]


class WashSaleAdjustment(models.Model):
    """Additive record (lots spec §2.5, D-37): disallowed loss on the
    losing match = basis addition on the replacement lot."""

    loss_match = models.ForeignKey(
        LotMatch, on_delete=models.CASCADE, related_name="wash_sale_adjustments"
    )
    replacement_lot = models.ForeignKey(
        Lot, on_delete=models.CASCADE, related_name="wash_sale_additions"
    )
    disallowed_loss = models.DecimalField(max_digits=40, decimal_places=18)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["WashSaleAdjustment"]] = models.Manager()


class StaleLotScope(models.Model):
    """A ledger edit touched this (account, instrument) pair; any query
    against it rebuilds transparently first (ADR-0032 §4)."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="+")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="+")
    marked_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[models.Manager["StaleLotScope"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "instrument"], name="uniq_stale_scope"),
        ]
