"""CurrencyMeta (instruments spec §3.1, ADR-0013 verbatim).

Categorization is the presence of this row: an Instrument with a
currency_meta IS a currency. No kind enum exists anywhere.
"""

from typing import ClassVar

from django.db import models

from django_assets.core.models import Instrument


class CurrencyMeta(models.Model):
    instrument = models.OneToOneField(
        Instrument, on_delete=models.CASCADE, related_name="currency_meta"
    )
    iso_code = models.CharField(max_length=3, unique=True)
    """ISO 4217 alphabetic code."""

    iso_numeric = models.PositiveSmallIntegerField(null=True, blank=True)
    symbol = models.CharField(max_length=8, blank=True)
    is_fiat = models.BooleanField(default=True)
    central_bank = models.CharField(max_length=100, blank=True)

    objects: ClassVar[models.Manager["CurrencyMeta"]] = models.Manager()

    def __str__(self) -> str:
        return self.iso_code
