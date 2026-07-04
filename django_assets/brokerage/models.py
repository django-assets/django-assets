"""Account policy: AccountProfile (brokerage spec §2, ADR-0014).

Capability flags are advisory — templates MAY consult them, the ledger
never does. allows_reconciliation is the one brokerage-ENFORCED flag: a
pre_save guard (wired in AppConfig.ready()) refuses to clear it while
ImportLine.matched_legs reference the account's legs.

Forward-compatibility per ADR-0014: no ENUMs, booleans default False,
no flag removals or default changes within a major, new vocabulary
values are documentation-only.
"""

from typing import Any, ClassVar

from django.apps import apps
from django.db import models

from django_assets.core.models import Account


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
