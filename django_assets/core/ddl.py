"""DDL installation for the non-table integrity layer (Product ADR-0004).

The canonical, idempotent .sql files under django_assets/sql/ are the single
source of truth. They are applied by three coordinated paths in "hybrid"
mode — the DDL migrations, the post_migrate handler below, and the
install_ledger_ddl management command — or by the host's own tooling in
"external" mode.
"""

from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.db import connections

SQL_ROOT = Path(__file__).resolve().parent.parent / "sql"

#: Application order matters: functions before triggers.
CATEGORIES = ("domains", "functions", "triggers")


def sql_files(direction: str = "up") -> list[Path]:
    """The canonical files, in application order."""
    prefix = "down_" if direction == "down" else ""
    files: list[Path] = []
    categories = CATEGORIES if direction == "up" else tuple(reversed(CATEGORIES))
    for category in categories:
        directory = SQL_ROOT / category
        if not directory.is_dir():
            continue
        names = sorted(
            p
            for p in directory.glob(f"{prefix}[0-9]*.sql" if not prefix else f"{prefix}*.sql")
            if p.name.startswith(prefix) and (prefix or not p.name.startswith("down_"))
        )
        files.extend(names if direction == "up" else reversed(names))
    return files


def apply_file(using: str, relative_path: str) -> None:
    """Execute one canonical file (idempotent by contract)."""
    sql = (SQL_ROOT / relative_path).read_text()
    with connections[using].cursor() as cursor:
        cursor.execute(sql)


def apply_all(using: str = "default") -> None:
    """(Re)apply every canonical file, in order. Safe to run repeatedly."""
    for path in sql_files():
        apply_file(using, str(path.relative_to(SQL_ROOT)))


def install_ddl(sender: AppConfig, using: str, **kwargs: Any) -> None:
    """post_migrate receiver (hybrid mode only; sender-filtered at connect).

    Re-applies the canonical DDL so --nomigrations test databases and
    freshly synced schemas get the integrity layer (ADR-0004).
    """
    apply_all(using=using)
