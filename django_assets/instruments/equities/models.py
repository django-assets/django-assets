"""EquityMeta (instruments spec §3.3, D-11 — minimal v0.x).

Richer fields (sector, share class, CEDEAR ratio links) arrive when a
concrete consumer does.
"""

from typing import ClassVar

from django.db import models

from django_assets.core.models import Exchange, Instrument


class EquityMeta(models.Model):
    instrument = models.OneToOneField(
        Instrument, on_delete=models.CASCADE, related_name="equity_meta"
    )
    primary_exchange = models.ForeignKey(Exchange, null=True, blank=True, on_delete=models.PROTECT)
    """The landing spot ADR-0009 assigned for the primary listing."""

    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["EquityMeta"]] = models.Manager()

    def __str__(self) -> str:
        return f"equity:{self.instrument.code}"
