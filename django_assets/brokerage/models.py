"""Brokerage models: account policy + import management
(brokerage spec §2/§5; ADR-0014/0019/0025/0026/0027).

Capability flags are advisory — templates MAY consult them, the ledger
never does. allows_reconciliation is the one brokerage-ENFORCED flag: a
pre_save guard (wired in AppConfig.ready()) refuses to clear it while
ImportLine.matched_legs reference the account's legs.

Forward-compatibility per ADR-0014: no ENUMs, booleans default False,
no flag removals or default changes within a major, new vocabulary
values are documentation-only.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from django.apps import apps
from django.conf import settings
from django.db import models

from django_assets.core.models import Account, Transaction, TransactionLeg

if TYPE_CHECKING:
    from django_assets.brokerage.schemas import ImportSchema


class AccountProfile(models.Model):
    account = models.OneToOneField(
        Account, on_delete=models.CASCADE, related_name="brokerage_profile"
    )
    subtype = models.CharField(max_length=40, blank=True, db_index=True)
    """Recommended vocabulary per ADR-0014; host-extensible; NOT DB-enforced."""

    allows_short = models.BooleanField(default=False)
    """Non-currency holdings may go negative; host-enforced."""

    allows_margin = models.BooleanField(default=False)
    """Currency holdings may go negative; host-enforced."""

    is_tax_advantaged = models.BooleanField(default=False, db_index=True)
    allows_reconciliation = models.BooleanField(default=False, db_index=True)
    """Brokerage-enforced: gates eligibility of the account's legs for
    ImportLine.matched_legs."""

    tax_treatment = models.CharField(max_length=40, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["AccountProfile"]] = models.Manager()

    def __str__(self) -> str:
        return f"profile:{self.account.name}"


class ImportBatch(models.Model):
    """One imported document (ADR-0019 as refined by ADR-0027).

    The schema natural key (broker, document_kind, format_kind, version)
    resolves through the registry — get_schema() — so historical batches
    can always name the exact parser that produced them (immortality
    convention).
    """

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="import_batches")
    schema_broker = models.SlugField(max_length=50)
    schema_document_kind = models.SlugField(max_length=50)
    schema_format_kind = models.CharField(max_length=20)
    schema_version = models.CharField(max_length=20)

    period_start = models.DateField(null=True, blank=True, db_index=True)
    period_end = models.DateField(null=True, blank=True, db_index=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    transaction_count = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["ImportBatch"]] = models.Manager()

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "schema_broker",
                    "schema_document_kind",
                    "schema_format_kind",
                    "schema_version",
                ],
                name="importbatch_schema_key_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.schema_broker}/{self.schema_document_kind} {self.file_name}"

    def get_schema(self) -> "ImportSchema":
        from django_assets.brokerage.schemas import registry

        return registry.get(
            self.schema_broker,
            self.schema_document_kind,
            self.schema_format_kind,
            self.schema_version,
        )


class TransactionImport(models.Model):
    """Provenance link from a Transaction back to its import batch."""

    transaction = models.OneToOneField(
        Transaction, on_delete=models.CASCADE, related_name="import_meta"
    )
    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="transaction_imports"
    )
    external_id = models.CharField(max_length=128, blank=True, db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    source_data = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["TransactionImport"]] = models.Manager()

    class Meta:
        constraints = [
            # Unique per batch when non-blank (spec §5.1).
            models.UniqueConstraint(
                fields=["batch", "external_id"],
                condition=~models.Q(external_id=""),
                name="uniq_external_id_per_batch",
            ),
        ]

    def __str__(self) -> str:
        return f"import:{self.external_id or self.transaction_id}"


class ImportLine(models.Model):
    """One line of raw imported evidence (ADR-0025/0026 shape verbatim).

    Matchable kinds are prefixed `broker_`; informational kinds
    (balance_snapshot, ytd_summary, …) are kept as evidence but never
    materialized. matched_legs is the reconciliation surface.
    """

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="lines")
    line_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=list, blank=True)
    kind = models.CharField(max_length=40)
    source_reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    matched_legs = models.ManyToManyField(
        TransactionLeg, related_name="reconciliation_lines", blank=True
    )
    metadata = models.JSONField(default=dict, blank=True)

    objects: ClassVar[models.Manager["ImportLine"]] = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "line_number"], name="uniq_line_number_per_batch"
            ),
        ]

    def __str__(self) -> str:
        return f"line {self.line_number} ({self.kind})"

    @property
    def is_matchable(self) -> bool:
        return self.kind.startswith("broker_")


def guard_reconciliation_flag(
    sender: type[AccountProfile], instance: AccountProfile, **kwargs: Any
) -> None:
    """pre_save: refuse clearing allows_reconciliation while matched legs
    exist (spec §2). ImportLine arrives with milestone B4 — until the
    model exists, there is nothing to guard."""
    if instance.pk is None or instance.allows_reconciliation:
        return
    try:
        current = AccountProfile.objects.get(pk=instance.pk)
    except AccountProfile.DoesNotExist:
        return
    if not current.allows_reconciliation:
        return  # was already off
    try:
        import_line = apps.get_model("django_assets", "ImportLine")
    except LookupError:
        return
    if import_line.objects.filter(matched_legs__account=instance.account).exists():
        raise ValueError(
            f"cannot clear allows_reconciliation on {instance.account.name!r}: "
            f"ImportLine matches reference this account's legs; unmatch them first"
        )
