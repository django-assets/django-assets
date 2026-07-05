"""Operational rebuild command (ADR-0032 §4): auto-rebuild-on-query is
the normal flow; this exists for bulk maintenance."""

from typing import Any

from django.core.management.base import BaseCommand

from django_assets.core.models import Account
from django_assets.lots.rebuild import rebuild_lots


class Command(BaseCommand):
    help = "Rebuild the lots book for every account (or --account ID)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--account", type=int, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        accounts = Account.objects.all()
        if options["account"]:
            accounts = accounts.filter(pk=options["account"])
        for account in accounts:
            rebuild_lots(account)
        self.stdout.write(self.style.SUCCESS(f"Rebuilt lots for {accounts.count()} accounts."))
