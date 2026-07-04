"""DDL install machinery (ADR-0004): idempotency and catalog state."""

import pytest
from django.core.management import call_command
from django.db import connection

from django_assets.core import ddl

pytestmark = pytest.mark.django_db


def _trigger_count():
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'transaction_legs_balanced'")
        return cur.fetchone()[0]


def _domains():
    with connection.cursor() as cur:
        cur.execute("SELECT typname FROM pg_type WHERE typname IN ('dec8', 'dec18') ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def test_migrations_installed_everything():
    assert _domains() == ["dec18", "dec8"]
    assert _trigger_count() == 1
    with connection.cursor() as cur:
        cur.execute(
            "SELECT domain_name FROM information_schema.columns "
            "WHERE table_name = 'django_assets_transactionleg' AND column_name = 'amount'"
        )
        assert cur.fetchone()[0] == "dec18"


def test_apply_all_is_idempotent():
    ddl.apply_all()
    ddl.apply_all()
    assert _trigger_count() == 1
    assert _domains() == ["dec18", "dec8"]


def test_management_command_idempotent():
    call_command("install_ledger_ddl")
    call_command("install_ledger_ddl")
    assert _trigger_count() == 1
