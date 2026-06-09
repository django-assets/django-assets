# ADR-0004: Install non-table DDL via hybrid migration + post_migrate, with external override

## Status

Accepted — 2026-06-07

## Context

`django-assets-core` requires database objects that Django's ORM cannot express:

- `NUMERIC` domains (`dec8`, `dec18`) for scale-checked decimal types
- Stored function `assert_transaction_balanced()`
- Deferred constraint trigger `transaction_legs_balanced`
- Range partitioning for `transaction_legs`
- Materialized views (planned for v0.4)
- Generated columns (where used)

Conventional Django practice is to install such DDL via `migrations.RunSQL` operations. This works for production deployments that run `manage.py migrate`.

However, the primary target host runs pytest with `--reuse-db --nomigrations` and sets `'TEST': {'MIGRATE': False}` in its test settings. Under these flags, `pytest-django` builds test tables via syncdb-style introspection of Django models and **does not execute migrations**. Anything that lives only in `migrations.RunSQL` is absent in the test database — including the balance trigger. Tests that should fail under integrity violations would silently pass.

This pattern (`--nomigrations` for test speed) is common across mature Django codebases. Any adopter using it will hit the same problem.

Three pure approaches were considered:

1. **Migration-only.** Convention-correct, supports `sqlmigrate`/`squashmigrations`/rollback, but absent under `--nomigrations`.
2. **`post_migrate` signal handler only.** Fires under `--nomigrations` (post_migrate runs after syncdb-built tables), but violates "schema lives in migration files," is invisible to migration tooling, has no inverse for rollback, and is skipped by `migrate --fake`.
3. **Pytest plugin only.** Works for tests, requires host opt-in in `conftest.py`, doesn't help production.

None alone is sufficient. A hybrid satisfies all audiences.

A fourth pattern was discovered during host-environment review: the primary target host manages all non-table DDL entirely outside Django. Triggers, functions, views, and stored procedures live as raw `.sql` files in a `database/` directory tree (organized by category), applied via shell scripts that loop over the files and pipe them into `psql`. No `post_migrate`, no `RunSQL`, no in-Django DDL of any kind for the domain-specific schema. Application tables use Django migrations; infrastructure DDL is deployment tooling.

The package's `.sql` file organization (per the canonical-SQL-file rule) is structurally compatible with this convention. The host could plug the package's `.sql` files into their existing shell-script flow without involving Django at all. Supporting this as a documented mode lets sophisticated adopters integrate the package's DDL into their own deployment tooling.

## Decision

### Canonical SQL files

DDL lives in raw `.sql` files organized by category, structurally matching the convention used by sophisticated Django hosts that manage non-table DDL outside Django:

```
django_assets/sql/
├── domains/
│   └── 001_dec_domains.sql
├── functions/
│   └── 001_assert_transaction_balanced.sql
├── triggers/
│   └── 001_transaction_legs_balanced.sql
└── views/
    └── (future materialized views)
```

The files are idempotent (see contract below) and can be applied by any tool that can pipe SQL into `psql`. No parallel copies of the DDL are maintained.

### Install mode is host-configurable

A setting `DJANGO_ASSETS_DDL_INSTALL_MODE` controls how the package's DDL is installed:

```python
# settings.py
DJANGO_ASSETS_DDL_INSTALL_MODE = "hybrid"   # default
# or "external"
```

**Mode `"hybrid"` (default)**: DDL is installed via three coordinated paths, all reading from the canonical `.sql` files:

1. **A real Django migration (`migrations.RunSQL`)** with `reverse_sql` set. Source of truth for production `manage.py migrate`, `manage.py sqlmigrate` visibility, `squashmigrations`, rollback, and DBA review.
2. **A `post_migrate` signal handler** wired in `AppConfig.ready()`, idempotently re-installing the same DDL. Catches `--nomigrations` test environments where the migration was skipped. The handler filters by `sender` (only fires for `django_assets`'s `AppConfig`) and respects the `using` kwarg in multi-database setups.
3. **A management command `manage.py install_ledger_ddl`**, idempotently installing the same DDL. Used to recover from `migrate --fake` adoption (where `post_migrate` does not fire) and as a manual repair tool if the DDL is dropped.

This is the right mode for any adopter without specialized DDL tooling.

**Mode `"external"` (opt-in)**: The migration is a no-op (or omits the `RunSQL` operation entirely via conditional generation); the `post_migrate` signal handler is not wired; the management command remains available for manual install. The host takes ownership of applying the `.sql` files using their own deployment tooling (e.g., the host's `db_sync_stored_procedures`-style shell scripts pointed at `site-packages/django_assets/sql/`).

This is the right mode for adopters whose existing change-control process manages non-table DDL outside Django entirely (like the primary target host, whose deployment tooling applies raw `.sql` files via shell scripts).

In external mode, CI for the package's own test suite still runs in `"hybrid"` mode — the package always validates that its DDL is correctly applied; external mode just transfers responsibility for *applying* it to the host.

### Idempotency contract

Regardless of install mode, the `.sql` files are idempotent (safe to re-run):

- `CREATE OR REPLACE FUNCTION ...` for stored functions.
- `DROP TRIGGER IF EXISTS ...; CREATE CONSTRAINT TRIGGER ...` for triggers (PostgreSQL does not support `CREATE TRIGGER IF NOT EXISTS`).
- `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dec8') THEN CREATE DOMAIN dec8 ...; END IF; END $$;` for domains.

This lets the post_migrate handler, shell scripts, and the management command all apply the same files repeatedly without errors.

### Python-layer fallback (separate setting)

When `DJANGO_ASSETS_USE_DB_TRIGGERS = False` (a separate setting for environments without DDL privileges entirely), the post_migrate handler skips installing the trigger and the package activates its Python-layer balance-check fallback. This is distinct from `DJANGO_ASSETS_DDL_INSTALL_MODE = "external"`, which assumes the DDL IS installed, just by the host's tooling.

The matrix:

| `DDL_INSTALL_MODE` | `USE_DB_TRIGGERS` | What happens |
| --- | --- | --- |
| `"hybrid"` | `True` | Migration + post_migrate install the DDL; trigger enforces balance at the DB level. (Default.) |
| `"external"` | `True` | Host's deployment tooling installs the `.sql` files; trigger enforces balance at the DB level. |
| Either | `False` | DB-level trigger not used; Python-layer pre-commit check enforces balance instead. For environments without DDL privileges. |

## Consequences

**Easier:**

- Production deployments using `migrate` install DDL the conventional way. DBAs reviewing migration files see all schema changes.
- Test environments using `--nomigrations` get the same DDL automatically. No host opt-in required for the common case.
- `sqlmigrate`, `squashmigrations`, and `migrate <app> zero` rollback all work because the canonical migration exists.
- The same DDL is reachable for manual install/repair via the management command.
- Sophisticated adopters with their own DDL deployment tooling can integrate the package's `.sql` files into their existing flow via the `"external"` mode without fighting the package.
- The `.sql` file layout is independently useful as documentation of what the package needs in the database.

**Harder:**

- Three install paths must be kept synchronized via the canonical SQL files. CI must verify all three paths produce the same database state.
- The DDL must be written idempotently (the migration could get away with non-idempotent SQL if it ran only once, but the `post_migrate` path requires idempotency).
- `migrate --fake` adoption still requires running `install_ledger_ddl` manually. This is documented but is a foot-gun for new adopters.
- Roughly 40 lines of glue code (signal handler, management command, file loader) live in the package indefinitely.
- Hosts that select `"external"` mode take on the responsibility of applying the `.sql` files themselves. If they forget after a package upgrade adds new DDL, integrity guarantees are silently absent until they remember.

## Verification

CI must include:

- A test run in `"hybrid"` mode with full migrations, verifying the migration installs the DDL.
- A test run in `"hybrid"` mode with `--reuse-db --nomigrations`, verifying the `post_migrate` handler installs the DDL.
- A test run in `"external"` mode that applies the `.sql` files via a `psql`-style harness (simulating a host's shell-script tooling) and confirms the resulting DB state matches.
- A test that `install_ledger_ddl` is idempotent and produces the same database state as the migration and the external-mode application.

If any of these fails, the hybrid contract has regressed.
