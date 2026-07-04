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
    # Reverse to the schema-only 0002 — this also unapplies every later
    # milestone's migrations, so the re-apply MUST target the leaf node
    # (not a hardcoded number) or the rest of the suite runs against a
    # half-migrated database.
    executor.migrate([("django_assets", "0002_add_transaction_and_leg")])
    executor.loader.build_graph()
    assert not _has_trigger()
    # Forward again, to the current leaf.
    executor = MigrationExecutor(connection)
    leaf = executor.loader.graph.leaf_nodes("django_assets")
    executor.migrate(leaf)
    assert _has_trigger()
