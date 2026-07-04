"""System checks: PostgreSQL-only, version floor (ADR-0001/0002)."""

from collections.abc import Iterable, Sequence
from typing import Any

from django.apps import AppConfig
from django.core.checks import Error, register
from django.db import connections

MINIMUM_PG_VERSION = 120000  # PostgreSQL 12 (ADR-0002)


def _check_connection(alias: str, connection: Any) -> list[Error]:
    if connection.vendor != "postgresql":
        return [
            Error(
                f"Database alias {alias!r} uses backend vendor "
                f"{connection.vendor!r}; django-assets supports PostgreSQL only.",
                hint="See product ADR-0001: integrity guarantees require PostgreSQL.",
                id="django_assets.E001",
            )
        ]
    if connection.pg_version < MINIMUM_PG_VERSION:
        return [
            Error(
                f"Database alias {alias!r} runs PostgreSQL "
                f"{connection.pg_version}; django-assets requires >= 12.",
                hint="See product ADR-0002 for the supported version floor.",
                id="django_assets.E002",
            )
        ]
    return []


@register("database")
def database_backend_check(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[Error]:
    """Runs with database checks (migrate, `check --database`)."""
    databases: Iterable[str] = kwargs.get("databases") or []
    errors: list[Error] = []
    for alias in databases:
        errors.extend(_check_connection(alias, connections[alias]))
    return errors
