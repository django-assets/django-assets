"""DDL migrations run forward AND reverse (PADR-0008)."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.ledger  # real schema changes need real commits


def _has_trigger():
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'transaction_legs_balanced'")
        return cur.fetchone()[0] == 1


def test_ddl_migrations_reverse_and_reapply():
    executor = MigrationExecutor(connection)
    assert _has_trigger()
    # Reverse all DDL migrations (back to the schema-only 0002).
    executor.migrate([("django_assets", "0002_add_transaction_and_leg")])
    executor.loader.build_graph()
    assert not _has_trigger()
    # Forward again.
    executor = MigrationExecutor(connection)
    executor.migrate([("django_assets", "0005_ddl_transaction_legs_balanced")])
    assert _has_trigger()
