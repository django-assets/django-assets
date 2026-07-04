"""DDL installation for the non-table integrity layer (Product ADR-0004).

The canonical .sql files live in django_assets/sql/. This module applies
them idempotently; it is reached via three coordinated paths in "hybrid"
mode (migration RunSQL, the post_migrate handler below, and the
install_ledger_ddl management command).
"""

from typing import Any

from django.apps import AppConfig


def install_ddl(sender: AppConfig, using: str, **kwargs: Any) -> None:
    """post_migrate receiver: idempotently (re)install the canonical DDL.

    Wired only in "hybrid" mode, filtered to this app's config via
    ``sender=`` at connect time, and applied against the ``using`` alias.
    The actual DDL arrives with core milestone C2; until then this is a
    deliberate no-op so the wiring contract is testable.
    """
    return
