"""Load the instrument seed fixtures (instruments spec §3, D-12).

Explicit, idempotent, dev-project / demo / host-bootstrap use only —
django-assets never auto-seeds reference data into adopter databases.
"""

from typing import Any

from django.core.management.base import BaseCommand

from django_assets.instruments.crypto import fixtures as crypto_fixtures
from django_assets.instruments.currencies import fixtures as currency_fixtures


class Command(BaseCommand):
    help = (
        "Seed the reference instruments (currencies, crypto) with metas and "
        "uppercase global identifiers. Explicit and idempotent (D-12)."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        currency_fixtures.load()
        crypto_fixtures.load()
        self.stdout.write(self.style.SUCCESS("Instrument fixtures loaded."))
