"""System check: PostgreSQL-only, version >= 12 (ADR-0001/0002)."""

from types import SimpleNamespace

from django_assets.core.checks import _check_connection


def test_real_connection_passes():
    from django.db import connection

    assert _check_connection("default", connection) == []


def test_non_postgres_backend_errors():
    fake = SimpleNamespace(vendor="sqlite")
    errors = _check_connection("default", fake)
    assert len(errors) == 1
    assert errors[0].id == "django_assets.E001"


def test_postgres_below_floor_errors():
    fake = SimpleNamespace(vendor="postgresql", pg_version=110013)
    errors = _check_connection("default", fake)
    assert len(errors) == 1
    assert errors[0].id == "django_assets.E002"
