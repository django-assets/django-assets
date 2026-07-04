"""Environment smoke test: the suite runs against real PostgreSQL >= 12.

Product ADR-0001 (Postgres-only) and ADR-0002 (PG 12 floor) make this the
ground truth every other test builds on. If this fails, nothing else matters.
"""

import pytest
from django.db import connection


@pytest.mark.django_db
def test_database_is_postgresql() -> None:
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_postgresql_version_is_at_least_12() -> None:
    major = connection.pg_version // 10000
    assert major >= 12, f"PostgreSQL {major} < 12 (ADR-0002 floor)"
