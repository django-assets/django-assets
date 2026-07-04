"""OptionMeta + Deliverable (instruments spec §3.4, ADR-0010 verbatim).

The derivative relationship (underlying) lives here, never in core. A
contract's deliverable composition changes over its lifetime via OCC
adjustments; validity is the half-open interval [effective_from,
effective_to) with NULL effective_to = +infinity.
"""

import datetime
from typing import ClassVar

from django.db import models

from django_assets.core.models import Instrument
from django_assets.instruments.models import CorporateAction


class OptionMeta(models.Model):
    instrument = models.OneToOneField(
        Instrument, on_delete=models.CASCADE, related_name="option_meta"
    )
    underlying = models.ForeignKey(
        Instrument, on_delete=models.PROTECT, related_name="option_meta_as_underlying"
    )
    expiry = models.DateField(db_index=True)
    strike = models.DecimalField(max_digits=20, decimal_places=8, db_index=True)
    right = models.CharField(max_length=1, choices=[("C", "Call"), ("P", "Put")])
    settlement_type = models.CharField(
        max_length=20,
        default="physical",
        choices=[("physical", "Physical"), ("cash", "Cash"), ("basket", "Basket")],
    )
    exercise_style = models.CharField(
        max_length=20,
        default="american",
        choices=[
            ("american", "American"),
            ("european", "European"),
            ("bermudan", "Bermudan"),
        ],
    )

    objects: ClassVar[models.Manager["OptionMeta"]] = models.Manager()

    def __str__(self) -> str:
        return f"{self.underlying.code} {self.expiry} {self.strike}{self.right}"

    def active_deliverables(self, on: datetime.date) -> "list[Deliverable]":
        """Rows in force at `on` per the half-open interval, in sequence
        order."""
        return list(
            self.deliverables.filter(
                models.Q(effective_from__lte=on),
                models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=on),
            ).order_by("sequence")
        )


class Deliverable(models.Model):
    """What one contract delivers on exercise: an instrument quantity XOR
    a cash amount (DB CHECK), valid for [effective_from, effective_to)."""

    option_meta = models.ForeignKey(
        OptionMeta, related_name="deliverables", on_delete=models.CASCADE
    )
    sequence = models.PositiveSmallIntegerField(default=0)

    instrument = models.ForeignKey(
        Instrument,
        null=True,
        blank=True,
        related_name="deliverable_components",
        on_delete=models.PROTECT,
    )
    quantity = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    cash_currency = models.ForeignKey(
        Instrument,
        null=True,
        blank=True,
        related_name="cash_deliverables",
        on_delete=models.PROTECT,
    )
    cash_amount = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)

    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    corporate_action = models.ForeignKey(
        CorporateAction,
        null=True,
        blank=True,
        related_name="deliverable_changes",
        on_delete=models.SET_NULL,
    )

    objects: ClassVar[models.Manager["Deliverable"]] = models.Manager()

    class Meta:
        constraints = [
            # check= (not condition=) because Django 4.2 is supported;
            # the stubs only know the 5.1+ spelling.
            models.CheckConstraint(  # type: ignore[call-arg]
                check=(
                    models.Q(
                        instrument__isnull=False,
                        quantity__isnull=False,
                        cash_currency__isnull=True,
                        cash_amount__isnull=True,
                    )
                    | models.Q(
                        instrument__isnull=True,
                        quantity__isnull=True,
                        cash_currency__isnull=False,
                        cash_amount__isnull=False,
                    )
                ),
                name="deliverable_either_instrument_or_cash",
            ),
        ]

    def __str__(self) -> str:
        if self.instrument_id is not None:
            return f"{self.quantity} {self.instrument}"
        return f"{self.cash_amount} {self.cash_currency}"
