"""Manual install/repair of the integrity DDL (Product ADR-0004).

Used to recover from `migrate --fake` adoption (where post_migrate never
fires) and as the entry point for hosts in "external" mode who want a
one-shot install. Idempotent.
"""

from typing import Any

from django.core.management.base import BaseCommand

from django_assets.core import ddl


class Command(BaseCommand):
    help = "Idempotently (re)install django-assets' domains, functions, and triggers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--database", default="default")

    def handle(self, *args: Any, **options: Any) -> None:
        ddl.apply_all(using=options["database"])
        self.stdout.write(self.style.SUCCESS("django-assets DDL installed."))
