"""Package settings accessors (Product ADR-0004; PADR-0011).

All DJANGO_ASSETS_* settings are read lazily through these helpers so
defaults live in exactly one place.
"""

from typing import Literal

from django.conf import settings

DdlInstallMode = Literal["hybrid", "external"]


def ddl_install_mode() -> DdlInstallMode:
    """How the package's non-table DDL is installed (ADR-0004).

    "hybrid" (default): migration + post_migrate handler + management command.
    "external": the host's own tooling applies the canonical .sql files.
    """
    mode: str = getattr(settings, "DJANGO_ASSETS_DDL_INSTALL_MODE", "hybrid")
    if mode not in ("hybrid", "external"):
        raise ValueError(
            f"DJANGO_ASSETS_DDL_INSTALL_MODE must be 'hybrid' or 'external', got {mode!r}"
        )
    return mode  # type: ignore[return-value]


def use_db_triggers() -> bool:
    """Whether DB-level triggers enforce integrity (ADR-0004).

    False activates the Python-layer balance-check fallback for environments
    without DDL privileges.
    """
    return bool(getattr(settings, "DJANGO_ASSETS_USE_DB_TRIGGERS", True))
