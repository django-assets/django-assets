"""Core models: the numeric-integrity schema (Product ADR-0020).

Exchange, Instrument, Identifier, Account here (C1); Transaction and
TransactionLeg arrive with the ledger milestone (C2). Core carries no
categorization, no policy, no opinion — see ADR-0020 for what deliberately
is NOT here.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

from django.conf import settings
from django.db import models, transaction

from django_assets.core.exceptions import ExcessPrecisionError


class Exchange(models.Model):
    """Exchange reference data. Shared catalog rows, never user-owned."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=40)
    """IANA timezone name; all stored timestamps remain UTC (ADR-0012)."""

    objects: ClassVar[models.Manager["Exchange"]] = models.Manager()

    def __str__(self) -> str:
        return self.code


class Instrument(models.Model):
    """A legal security / unit of value (ADR-0009, ADR-0013).

    Identity is independent of venue; resolution goes through Identifier.
    Every unit of value — fiat, crypto, equity, option — is an Instrument
    treated uniformly by the ledger. Core knows precision rules and nothing
    about categories (ADR-0020).
    """

    code = models.CharField(max_length=64, db_index=True)
    """Display convenience; NOT unique — resolution uses Identifier."""

    quantity_decimals = models.PositiveSmallIntegerField(default=4)
    price_decimals = models.PositiveSmallIntegerField(default=4)
    multiplier = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    price_currency = models.ForeignKey(
        "self", related_name="+", null=True, blank=True, on_delete=models.PROTECT
    )
    """Unit this instrument is priced in; NULL allowed for base currencies."""

    is_active = models.BooleanField(default=True, db_index=True)
    """Flips only when no venue lists the security at all (ADR-0009)."""

    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["Instrument"]] = models.Manager()

    def __str__(self) -> str:
        return self.code

    def _quantize(self, value: Decimal, decimals: int, strict: bool) -> Decimal:
        exponent = Decimal(1).scaleb(-decimals)
        quantized = value.quantize(exponent, rounding=ROUND_HALF_UP)
        if strict and quantized != value:
            raise ExcessPrecisionError(
                f"{value} carries more precision than {self.code}'s "
                f"{decimals} decimal places; ledger amounts are never "
                f"silently rounded (pass an exact amount)."
            )
        return quantized

    def quantize(self, amount: Decimal, *, strict: bool = False) -> Decimal:
        """Quantize a quantity to this instrument's scale (ROUND_HALF_UP).

        strict=True is the ledger-write posture (D-5): raises
        ExcessPrecisionError instead of changing the value.
        """
        return self._quantize(amount, self.quantity_decimals, strict)

    def quantize_price(self, price: Decimal, *, strict: bool = False) -> Decimal:
        """Quantize a price to this instrument's price scale (D-1)."""
        return self._quantize(price, self.price_decimals, strict)

    def rename_identifier(
        self,
        old_value: str,
        new_value: str,
        *,
        on: datetime.date,
        type: str = "ticker",
    ) -> "Identifier":
        """Atomically retire an identifier and create its replacement.

        The ADR-0009 hygiene helper: the old row is deactivated with
        effective_to=on; the new row starts at effective_from=on on the
        same exchange. Raises Identifier.DoesNotExist (or
        MultipleObjectsReturned when the value is ambiguous across
        exchanges) rather than guessing.
        """
        with transaction.atomic():
            old = self.identifiers.get(type=type, value=old_value, is_active=True)
            old.is_active = False
            old.effective_to = on
            old.save(update_fields=["is_active", "effective_to"])
            return self.identifiers.create(
                type=type,
                value=new_value,
                exchange=old.exchange,
                is_active=True,
                effective_from=on,
            )


class Identifier(models.Model):
    """A symbol/identifier mapping for an Instrument (ADR-0009).

    Exchange-scoped types (ticker, opra) set `exchange`; global types
    (isin, cusip, figi, sedol) leave it NULL. History is date-based
    (effective_from/effective_to) — deliberately no chain FKs.
    """

    instrument = models.ForeignKey(Instrument, related_name="identifiers", on_delete=models.CASCADE)
    type = models.CharField(max_length=20)
    value = models.CharField(max_length=64)
    exchange = models.ForeignKey(Exchange, null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    objects: ClassVar[models.Manager["Identifier"]] = models.Manager()

    class Meta:
        constraints = [
            # One ACTIVE identifier per (type, value, exchange); inactive
            # rows may duplicate freely (ticker reuse, ADR-0009).
            models.UniqueConstraint(
                fields=["type", "value", "exchange"],
                condition=models.Q(is_active=True),
                name="uniq_active_identifier",
            ),
            # PG 12 lacks NULLS NOT DISTINCT (ADR-0002), so NULL-exchange
            # (global) identifiers need their own active-uniqueness rule.
            models.UniqueConstraint(
                fields=["type", "value"],
                condition=models.Q(is_active=True, exchange__isnull=True),
                name="uniq_active_global_identifier",
            ),
        ]
        indexes = [
            # Resolver hot path (ADR-0009, D-2).
            models.Index(
                fields=["type", "value", "exchange", "is_active"],
                name="identifier_resolve_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type}:{self.value}"


class Account(models.Model):
    """A bucket that holds value: brokerage, bank, wallet, tracking account.

    Exactly one owner (ADR-0005). CASCADE on user hard-delete is deliberate:
    GDPR Article 17 erasure is one User.delete() call (ADR-0006) — document
    prominently. Core attaches no subtype or capability flags (ADR-0020);
    roles like "external counterparty" are naming conventions.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="accounts",
        db_index=True,
    )
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["Account"]] = models.Manager()

    def __str__(self) -> str:
        return self.name
