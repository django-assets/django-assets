#!/usr/bin/env python
"""ADR-0004 verification: all install paths converge to the same DB state.

Compares catalog signatures between (A) a database built by `migrate` in
hybrid mode and (B) a database built in external mode with the canonical
.sql files applied the way a host's shell tooling would. Also proves
install_ledger_ddl idempotency on A.

Requires a running PostgreSQL superuser (the compose/CI default). Exits
non-zero on divergence.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg

HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PORT = os.environ.get("POSTGRES_PORT", "5432")
USER = os.environ.get("POSTGRES_USER", "django_assets")
PASSWORD = os.environ.get("POSTGRES_PASSWORD", "django_assets")
ADMIN_DB = os.environ.get("POSTGRES_DB", "django_assets_test")

SIGNATURE_SQL = """
SELECT 'domain:' || t.typname || ':' || pg_get_constraintdef(c.oid)
FROM pg_type t JOIN pg_constraint c ON c.contypid = t.oid
WHERE t.typname IN ('dec8', 'dec18')
UNION ALL
SELECT 'function:' || proname || ':' || md5(prosrc)
FROM pg_proc WHERE proname = 'assert_transaction_balanced'
UNION ALL
SELECT 'trigger:' || tgname || ':' || tgdeferrable::text || ':' || tginitdeferred::text
FROM pg_trigger WHERE tgname = 'transaction_legs_balanced'
UNION ALL
SELECT 'column:' || coalesce(domain_name, data_type)
FROM information_schema.columns
WHERE table_name = 'django_assets_transactionleg' AND column_name = 'amount'
ORDER BY 1
"""


def admin(sql: str) -> None:
    with psycopg.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=ADMIN_DB, autocommit=True
    ) as conn:
        conn.execute(sql)


def signature(dbname: str) -> list[str]:
    with psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=dbname) as conn:
        return [r[0] for r in conn.execute(SIGNATURE_SQL).fetchall()]


def manage(dbname: str, *args: str, mode: str = "hybrid") -> None:
    env = os.environ | {
        "POSTGRES_DB": dbname,
        "POSTGRES_HOST": HOST,
        "DJANGO_SETTINGS_MODULE": "dev_project.settings.test",
    }
    if mode == "external":
        env["DJANGO_ASSETS_DDL_INSTALL_MODE"] = "external"
    subprocess.run([sys.executable, "manage.py", *args], env=env, check=True, capture_output=True)


def apply_sql_like_a_shell_script(dbname: str) -> None:
    """The host-tooling simulation: loop the files into the database."""
    sql_root = Path("django_assets/sql")
    with psycopg.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=dbname, autocommit=True
    ) as conn:
        for category in ("domains", "functions", "triggers"):
            for path in sorted((sql_root / category).glob("[0-9]*.sql")):
                conn.execute(path.read_text())


def main() -> int:
    for db in ("ddl_verify_a", "ddl_verify_b"):
        admin(f'DROP DATABASE IF EXISTS "{db}"')
        admin(f'CREATE DATABASE "{db}"')

    # Path A: hybrid migrate.
    manage("ddl_verify_a", "migrate", "--no-input")
    sig_a = signature("ddl_verify_a")

    # Idempotency: the repair command twice changes nothing.
    manage("ddl_verify_a", "install_ledger_ddl")
    manage("ddl_verify_a", "install_ledger_ddl")
    if signature("ddl_verify_a") != sig_a:
        print("FAIL: install_ledger_ddl is not idempotent")
        return 1

    # Path B: external mode (DDL migrations no-op) + host-style .sql apply.
    manage("ddl_verify_b", "migrate", "--no-input", mode="external")
    if "trigger:transaction_legs_balanced:t:t" in "\n".join(signature("ddl_verify_b")):
        print("FAIL: external-mode migrate installed the trigger (should no-op)")
        return 1
    apply_sql_like_a_shell_script("ddl_verify_b")
    sig_b = signature("ddl_verify_b")

    if sig_a != sig_b:
        print("FAIL: hybrid and external install paths diverge")
        print("A:", sig_a)
        print("B:", sig_b)
        return 1
    if not any(s.startswith("trigger:") for s in sig_a):
        print("FAIL: trigger missing from both paths")
        return 1
    print("OK: hybrid migrate == external .sql apply; installer idempotent")
    print("\n".join(sig_a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
