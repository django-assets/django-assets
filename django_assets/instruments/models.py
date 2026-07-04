"""Package-root shared models (instruments spec §2, ADR-0010/0033).

Only genuinely cross-type material lives here: a corporate action can
adjust equities AND open options, so CorporateAction is shared. Type
packages never import each other — cross-type relationships go through
core Instrument FKs or these root models.
"""

from typing import ClassVar

from django.db import models

from django_assets.core.models import Instrument


class CorporateAction(models.Model):
    """One corporate event (ADR-0010): the schema is defined here;
    populating rows is host / sibling-package work (ADR-0011)."""

    effective_date = models.DateField(db_index=True)
    action_type = models.CharField(max_length=40)
    """Open vocabulary: spinoff, split, reverse_split, merger, acquisition,
    special_dividend, symbol_change, exchange_change, option_adjustment,
    delisting, … — no enum, by design."""

    source_reference = models.CharField(max_length=200, blank=True)
    """External provenance, e.g. 'OCC #47935'."""

    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    primary_instrument = models.ForeignKey(
        Instrument,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="corporate_actions",
    )

    objects: ClassVar[models.Manager["CorporateAction"]] = models.Manager()

    def __str__(self) -> str:
        return f"{self.action_type} {self.effective_date}"
