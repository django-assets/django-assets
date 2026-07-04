"""CryptoMeta (instruments spec §3.2, ADR-0013 verbatim)."""

from typing import ClassVar

from django.db import models

from django_assets.core.models import Instrument


class CryptoMeta(models.Model):
    instrument = models.OneToOneField(
        Instrument, on_delete=models.CASCADE, related_name="crypto_meta"
    )
    symbol = models.CharField(max_length=20)
    network = models.CharField(max_length=50, blank=True)
    contract_address = models.CharField(max_length=100, blank=True)
    is_stablecoin = models.BooleanField(default=False)
    pegged_to = models.ForeignKey(
        Instrument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pegged_cryptos",
    )
    """The Instrument this stablecoin tracks (cross-type via core FK)."""

    objects: ClassVar[models.Manager["CryptoMeta"]] = models.Manager()

    def __str__(self) -> str:
        return self.symbol
