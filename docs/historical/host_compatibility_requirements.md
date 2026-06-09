# django-assets — Host Compatibility Requirements

## Purpose

This document captures the requirements that the `django-assets` distribution must meet in order to be drop-in installable into the primary target host's Django project.

The primary target host is the intended environment for a future first-party portfolio app that will be built on top of `django_assets.core`. Today, no portfolio code lives in the host repo; the goal is to develop the `django-assets` distribution as a standalone reusable package now, then add a thin portfolio app inside the host later that consumes it.

This document lists the constraints discovered by surveying the host repo's actual configuration. Every requirement is something the package authors must design for now — retrofitting any of these later is significantly more costly than designing for them from day one.

Requirements are labeled `REQ-N` for traceability. **MUST** = hard requirement, will break installation. **SHOULD** = strong recommendation, will cause integration pain if violated.

## Host Environment Summary

- **Framework**: Django 4.2 LTS, DRF 3.14
- **Python**: 3.12
- **Database**: PostgreSQL 12.x (single primary), reached via pgBouncer in transaction-pooling mode; psycopg 3.x driver. TimescaleDB extension is installed on the primary alongside common diagnostic extensions (`plpgsql`, `pg_stat_statements`, `pgstattuple`, `pg_prewarm`).
- **Caching**: `django-cachalot` wraps the ORM; Redis backends for `default` and `cachalot` aliases
- **Auth**: Django's default `auth.User` (no custom `AUTH_USER_MODEL`); DRF token auth available, no global authentication classes set
- **Schema**: `drf-spectacular` auto-generates OpenAPI from serializers
- **Test runner**: pytest with `--reuse-db --nomigrations` — tables created via `syncdb` from model definitions, not via migrations
- **Timezone**: `USE_TZ=True`, `TIME_ZONE='UTC'`
- **App layout**: top-level packages under the host's Django project root; URLs wired via `router.registry.extend(...)`

## Section 1: Runtime Version Compatibility (Hard Requirements)

### REQ-1: Support Django 4.2 LTS — MUST

The host pins Django 4.2 LTS in its requirements. The package must target **Django 4.2 LTS** as the minimum supported version.

Concretely this means avoiding 5.x-only ORM features:

- No `models.GeneratedField` (added in Django 5.0). Use raw SQL migrations or domains for computed columns.
- No `Composite Primary Keys` (added in 5.2). Use single-column PKs everywhere.
- No `db_default=` on fields (added in 5.0). Use `default=` only.
- No `Q` reference shortcut updates from 5.0+.

Document the supported Django range as `>=4.2,<6.0` in `pyproject.toml`.

### REQ-2: Support PostgreSQL 12 — MUST

The host's primary database is **PostgreSQL 12.x** (confirmed via `SELECT version()`). TimescaleDB is installed as an extension on the same database. Target **PostgreSQL >= 12** as the floor.

Avoid features added after PG 12:

- No multirange types (`int4multirange`, `tstzmultirange`) — PG 14+
- No SQL/JSON `JSON_TABLE`, `JSON_QUERY` — PG 17+
- No `pg_stat_io` — PG 16+
- No `MERGE` statement — PG 15+
- No `NULLS NOT DISTINCT` — PG 15+
- No `GENERATED ALWAYS AS IDENTITY` improvements from later versions — basic identity columns work on PG 10+ ✓

PG 12 supports everything in the README's current design (`NUMERIC` domains, deferred constraint triggers, declarative partitioning, generated columns, `scale()` in CHECKs). The design is fine — just lock the version floor explicitly in package documentation and CI.

The package MUST NOT depend on the TimescaleDB extension (see Section 8 for context on how the host may opt in independently).

### REQ-3: Be compatible with pgBouncer transaction-pooling mode — MUST

The host connects through pgBouncer in transaction-pooling mode (port 6432, `DISABLE_SERVER_SIDE_CURSORS=True`, `prepare_threshold=0`). This forbids any feature relying on session-level state persisting across queries:

- **No `LISTEN/NOTIFY`** — cannot be used for cache invalidation, cross-process signaling, or any pub/sub pattern.
- **No session-scoped advisory locks** — if locks are needed, use `pg_advisory_xact_lock()` (transaction-scoped) only.
- **No persistent server-side cursors** — fine for ORM queries that fetch all rows; avoid features assuming cursor reuse.
- **No `SET` outside a transaction** — any session-level configuration (`SET search_path`, `SET timezone`, `SET LOCAL`) must be inside the transaction that uses it.
- **Temporary tables die at COMMIT** — cannot be used for multi-query workflows.

The deferred `assert_transaction_balanced` trigger is fine because `DEFERRABLE INITIALLY DEFERRED` is a per-transaction property declared in DDL, not session state.

### REQ-4: Use psycopg 3 idioms — MUST

The host uses `psycopg[binary]>=3.1.14`. Any low-level psycopg usage (raw cursors, COPY, custom type adapters) must work with psycopg 3, not psycopg2.

### REQ-5: Support DRF 3.14.0 — MUST

The host pins `djangorestframework==3.14.0`. Avoid features added in DRF 3.15+ (e.g., new field arguments, schema helpers introduced in later versions). Serializers and viewsets must work on 3.14.

## Section 2: Test Infrastructure Compatibility (Hard Requirements)

### REQ-6: Function correctly under `pytest --nomigrations` — MUST

The host runs pytest with `--reuse-db --nomigrations` (configured in `pytest.ini`) and sets `'TEST': {'MIGRATE': False}` in its test settings. This means **pytest-django creates tables via `syncdb` from model definitions and does not execute migrations**.

Anything that lives only in raw SQL migrations will not exist during tests in this environment:

- `dec8`, `dec18`, and other `NUMERIC` domains
- The `assert_transaction_balanced` deferred constraint trigger
- Generated columns added via SQL
- Partition setup for `transaction_legs`
- Materialized views

**Required strategy: hybrid migration + `post_migrate` installation.**

All non-table DDL (domains, functions, deferred constraint triggers, partitioning, generated columns, materialized views) MUST be installed via **both** of the following paths, reading from a single canonical SQL source:

1. **A real Django migration** (`migrations.RunSQL`) with `reverse_sql` set, so production deployments install the DDL via `manage.py migrate` and `manage.py sqlmigrate` shows it. This preserves Django's "all schema changes are visible in migration files" convention. The migration is the source of truth for `squashmigrations`, rollback, and DBA review.
2. **A `post_migrate` signal handler** wired in `AppConfig.ready()` that executes the same DDL idempotently. `post_migrate` fires after pytest-django's `syncdb`-based table creation under `--nomigrations`, so the trigger gets installed in test environments where the migration was skipped.

The package MUST keep the SQL in a single `.sql` file (e.g., `django_assets_core/sql/balance_trigger.sql`) and have both the migration and the `post_migrate` handler load from it. Do not maintain two parallel copies of the SQL.

Idempotency requirements for the `post_migrate` path:

- Use `CREATE OR REPLACE FUNCTION` for stored functions (safe to re-run).
- Use `DROP TRIGGER IF EXISTS ...; CREATE CONSTRAINT TRIGGER ...` for triggers (PostgreSQL does not support `CREATE TRIGGER IF NOT EXISTS`).
- Wrap `CREATE DOMAIN` statements in a `DO $$ ... $$` block that checks `pg_type` first, since `CREATE DOMAIN IF NOT EXISTS` does not exist.
- The `post_migrate` handler MUST filter by `sender` so it only fires for `django_assets_core`'s `AppConfig`, not on every other app's migrations.
- The handler MUST respect the `using` kwarg in multi-database deployments and only install on the database routed for `django_assets_core` models.

### REQ-6a: Provide an explicit install command for `migrate --fake` scenarios — MUST

`manage.py migrate --fake` records migrations as applied without running them and does **not** fire `post_migrate`. Teams adopting the package against an existing database with the tables already present may use `--fake`, in which case the trigger will not be installed.

The package MUST ship a management command, e.g. `manage.py install_ledger_ddl`, that idempotently installs the same DDL. Document this command as required-after-`--fake` and as a recovery tool if the DDL is ever accidentally dropped.

### REQ-6b: Optional Python-layer fallback for restricted environments — SHOULD

For environments where DDL cannot be applied at all (managed PostgreSQL instances without `CREATE FUNCTION` privilege), the package SHOULD also ship a Python-layer balance enforcement path activated by `DJANGO_ASSETS_USE_DB_TRIGGERS = False`. When that setting is `False`:

- The `post_migrate` handler skips installing the trigger.
- A `transaction.on_commit` (or equivalent pre-commit) hook performs the per-instrument balance check in Python and raises `UnbalancedTransactionError`.

This is a defense-in-depth complement to the hybrid path, not a replacement. It is bypassable by any raw SQL write that does not go through Django's transaction lifecycle. Document the limitation loudly.

### REQ-7: Be safe under `--reuse-db` — MUST

The host re-uses the test database across runs. Any DDL the package emits (via pytest plugin, signal, or migration) must be **idempotent** — use `CREATE DOMAIN IF NOT EXISTS`, `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` patterns. No `CREATE` that fails on re-run.

## Section 3: Cache Layer Compatibility (Hard Requirements)

### REQ-8: All writes must go through the Django ORM, OR expose an invalidation API — MUST

The host has `django-cachalot 2.7.0` in `INSTALLED_APPS`. Cachalot transparently caches every ORM read and invalidates cached entries on every ORM write. **Any path that bypasses the ORM for writes will leave cachalot stale.**

Risky surfaces in the current design:

- Raw SQL inserts into `transaction_legs` (`COPY`, `INSERT ... SELECT`, bulk `executemany`)
- Partman-style background partition moves
- Materialized view refreshes (`REFRESH MATERIALIZED VIEW`)
- Direct trigger-side mutations (the balance trigger only raises, so it is safe; future triggers must not silently mutate)

**Requirement**: either route all writes through the ORM (`Model.objects.create`, `bulk_create`, `update`), OR expose a public helper such as `django_assets_core.cache.invalidate(*models)` that wraps `cachalot.api.invalidate()` so the host can call it consistently after any raw-SQL write path. Document which surfaces require manual invalidation.

### REQ-9: Document `INSTALLED_APPS` ordering relative to `cachalot` — MUST

Cachalot only caches models from apps listed *before* `'cachalot'` in `INSTALLED_APPS`. The README must specify whether `django_assets_core` should appear before or after `cachalot` (recommended: **before**, so ledger reads are cached).

### REQ-10: Do not use Django's cache framework for hot-path internal caching — SHOULD

The host's `default` cache uses a custom `KEY_FUNCTION` and a `cachalot`-dedicated alias. Avoid using `django.core.cache` for the package's own caching needs; if caching is needed (e.g., in price connectors), do it in-memory or expose a connector-level cache wrapper (the README's `CachedPriceConnector` pattern is correct).

## Section 4: Auth, DRF, and Schema Compatibility (Hard Requirements)

### REQ-11: Never import the user model — MUST

Always reference the user model via `settings.AUTH_USER_MODEL` (string), never via `from django.contrib.auth.models import User` or `from <host_app> import User`. The host does not override `AUTH_USER_MODEL`, but the package must not assume that.

`Account` should `ForeignKey(settings.AUTH_USER_MODEL, ...)`. The User-to-Account relationship (one-to-many: one user owns many accounts) belongs in the host's adapter app, not in core.

### REQ-12: Ship no global authentication or permission classes — MUST

The host sets `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = ()` in its base settings. Every viewset in the host wires its own `authentication_classes` and `permission_classes` explicitly.

The package's views (if any are shipped) **must not assume** that any authentication is configured globally. Either:

- Ship views with `permission_classes = [IsAuthenticated]` explicitly and document the assumption, or
- Ship no views — provide only models, serializers, and helper APIs, and let the host build its own viewsets.

The latter is strongly preferred for core; brokerage and trades may ship admin-facing views.

### REQ-13: Custom exception interoperability — MUST

The host installs its own DRF exception handler at `common.util.exceptions.custom_exception_handler`. The package's exceptions (`UnbalancedTransactionError`, `PriceNotFoundError`, `PriceConnectorError`, scale-violation errors) must satisfy at least one of:

- Subclass `rest_framework.exceptions.APIException` so the host's handler renders them with the right status code, or
- Be caught and re-raised by the package's own view layer before bubbling up.

Document each exception's intended HTTP status mapping.

### REQ-14: drf-spectacular schema compatibility — MUST

The host generates OpenAPI via `drf-spectacular` (`REST_FRAMEWORK['DEFAULT_SCHEMA_CLASS']` and `SPECTACULAR_SETTINGS` in its base settings). The package's serializers must produce clean schema:

- The `Measure` DRF field must declare its OpenAPI type via `@extend_schema_field` or by subclassing in a way spectacular can introspect. Example shape: `{ "amount": "string (decimal)", "unit": "string" }`.
- Any polymorphic serializer (e.g., asset-type-specific metadata) must use spectacular's `PolymorphicProxySerializer` or equivalent, not raw `SerializerMethodField` returning untyped dicts.
- Avoid `JSONField` with no schema hint — declare an inner shape when known, or annotate as `additionalProperties: true`.

Run `manage.py spectacular --validate` in the standalone `dev_project/` to verify.

### REQ-15: Expose a `router` symbol from `urls.py`, do not self-mount — MUST

The host wires URLs by importing each app's DRF router and extending its top-level routers:

```python
# in the host's urls.py
from django_assets_core.urls import router as ledger_router
router.registry.extend(ledger_router.registry)
```

The package's `urls.py` must expose `router` (a `rest_framework.routers.DefaultRouter` or `SimpleRouter` instance) and **must not** include itself in any URL conf — the host owns mounting decisions (path prefix, version, etc.).

## Section 5: Code Organization and Naming (Hard Requirements)

### REQ-16: Import path `django_assets_core` (and siblings) at top level — MUST

The host's apps are top-level Python packages (`options`, `user`, `stock`, etc.). pip-installed packages live in `site-packages/django_assets_core/`, so this is naturally satisfied. **Do not** nest the importable package under a vendor namespace (e.g., `acmecorp.django_assets_core`).

### REQ-17: Standard `app_label` and migrations location — MUST

- `apps.py::AppConfig.name = "django_assets_core"` (matches README).
- Migrations live in `django_assets_core/migrations/` (the default location). The host does not override `MIGRATION_MODULES` and the package must not require it to.

### REQ-18: `DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'` — MUST

Already matches the host's `DEFAULT_AUTO_FIELD` setting and the package README. No action needed beyond verifying it stays this way.

### REQ-19: Wire signals via `AppConfig.ready()` — SHOULD

Follow the host's convention (`options/apps.py::OptionsConfig.ready`) of importing the signals module from `ready()` rather than at module top level. This avoids import-time side effects.

### REQ-20: Reserved name check — no collisions found

The host has no apps named `accounts`, `assets`, `instruments`, `portfolio`, `trade`, `ledger`, or `holdings`. The README's planned model names are safe.

## Section 6: Timezone, Decimal, and Type Constraints (Hard Requirements)

### REQ-21: All timestamps tz-aware UTC — MUST

The host runs `USE_TZ=True`, `TIME_ZONE='UTC'`. All `DateTimeField` columns must store `TIMESTAMPTZ`. In Python:

- Use `django.utils.timezone.now()` exclusively; never `datetime.now()` or `datetime.utcnow()`.
- Constructors that accept user-provided timestamps must reject naive datetimes with a clear error, or normalize them with an explicit assumption (preferably reject — the host has a recent history of ET/UTC drift bugs).

### REQ-22: No `float` anywhere in the API — MUST

Already in the README's core principles. Enforce concretely:

- DRF response payloads use `serializers.DecimalField`, never `FloatField`. Even read-only computed values.
- Type annotations use `Decimal`, never `float`.
- JSON deserialization paths quantize via `Decimal(str(value))`, not `Decimal(value)` from float.
- Add a unit test that asserts no `float` field type appears in any serializer.

### REQ-23: Quantization uses `ROUND_HALF_UP` — MUST

Matches README. Document the rounding mode for every public arithmetic helper.

## Section 7: Host-Overridable Settings (Pluggability Requirements)

The package must expose the following settings as dotted-path strings or simple values, so the host can swap implementations without forking. **Default to safe in-package implementations** so the package works out-of-the-box.

### REQ-24: `DJANGO_ASSETS_INSTRUMENT_RESOLVER` — MUST

```python
DJANGO_ASSETS_INSTRUMENT_RESOLVER = "django_assets_core.resolvers.DefaultResolver"
```

The host will override this to handle OCC option symbols, broker-specific tickers, and the existing symbol normalization rules already implemented in `options.services.option_helpers` and the stock symbol pipeline. The resolver interface must be a single method, e.g.:

```python
class InstrumentResolver(Protocol):
    def resolve(self, code: str, hint: str | None = None) -> Instrument: ...
```

Symbol normalization is the most likely point of fork pressure if the resolver is not pluggable.

### REQ-25: `DJANGO_ASSETS_PRICE_CONNECTOR` — MUST

```python
DJANGO_ASSETS_PRICE_CONNECTOR = "django_assets.core.connectors.ExampleHTTPConnector"
```

The host will override this with an in-process connector that reuses the host's own services directly, avoiding an HTTP round-trip to an external market-data API the host already owns. The connector interface is already defined in `django_assets_core_price_connectors_guide.md`.

### REQ-26: `DJANGO_ASSETS_USE_DB_TRIGGERS` — MUST

```python
DJANGO_ASSETS_USE_DB_TRIGGERS = True  # default
```

When `False`, the `post_migrate` handler skips installing the trigger and the package falls back to Python-layer balance enforcement (see REQ-6b). Used by environments without DDL access.

### REQ-27: `DJANGO_ASSETS_DB_ALIAS` — SHOULD

```python
DJANGO_ASSETS_DB_ALIAS = "default"
```

For future multi-DB deployments. Currently the host is single-DB, but the historical Timescale replica is a separate DB; a future portfolio archive could live on it. All queries inside the package should respect this alias via `.using(alias)`.

### REQ-28: `DJANGO_ASSETS_DEFAULT_OPTION_MULTIPLIER` — SHOULD

```python
from decimal import Decimal
DJANGO_ASSETS_DEFAULT_OPTION_MULTIPLIER = Decimal("100")
```

Already implied by README. Expose as override for index options (SPX uses 100; some products use 10 or 1000).

### REQ-29: `DJANGO_ASSETS_REFRESH_TASK` — SHOULD

```python
DJANGO_ASSETS_REFRESH_TASK = None  # default: no async refresh
```

For environments with Celery. If set to a dotted task path, the package will dispatch end-of-day materialized view refreshes (or holdings recalculation, etc.) via that task. The host has `django_celery_beat` and `django_celery_results` available; it will wire its own task and point this setting at it.

**Important**: the package must not import `celery` at module top level. The dispatch path must be late-bound and fall back to synchronous execution when the setting is `None`.

## Section 8: Things the Host Has That the Package Should Be Aware Of

These are not requirements on the package — they are context that should inform documentation.

- **TimescaleDB is installed on the primary database** (not only on the separate historical replica). The package's declarative-partitioning plan for `transaction_legs` works on plain PG 12 and must not require TimescaleDB. However, because the extension is already present in the host environment, a future optional sibling package could offer a TimescaleDB-hypertable variant for `transaction_legs` without requiring infra changes. Keep this in mind when designing the partitioning interface — leave room for an alternative implementation to be swapped in.
- **Single primary DB via pgBouncer.** See REQ-3 for the implications.
- **Celery is available** but the package must not require it.
- **The host already has `Instrument`-like concepts** for stocks and options (`stock.*` symbols, `options.models.OptionContract`). The host-side adapter app will reconcile these to `django_assets_core.Instrument` records — likely lazy-creation on first transaction. This is host work, not package work.

## Section 9: Standalone Development Project Requirements

### REQ-30: Ship a `dev_project/` for standalone development — MUST

The package must be developable and testable independently of the host. Provide a minimal Django project under the package repo root (`dev_project/`):

```
django-assets/
├── django_assets_core/         # the package
├── dev_project/
│   ├── manage.py
│   ├── settings.py             # PostgreSQL only (see REQ-31)
│   ├── urls.py                 # mounts core router under /
│   └── conftest.py             # enables pytest plugin from REQ-6
└── pyproject.toml
```

`dev_project/settings.py` minimum installed apps: `django.contrib.{auth,contenttypes,admin,sessions,messages,staticfiles}`, `rest_framework`, `django_assets_core`.

### REQ-31: Standalone dev uses PostgreSQL only — MUST

The package's integrity guarantees (domains, deferred triggers, partitioning) are PostgreSQL-only. `dev_project/settings.py` must configure a PostgreSQL backend exclusively — no other database engine is supported by the package and `dev_project/` must not pretend otherwise.

Document the local Postgres setup (Docker one-liner) in the package README. The same PostgreSQL version targeted by the host environment should be used in `dev_project/`, so behavior observed locally matches behavior in the host.

### REQ-32: `dev_project/` must run pytest with `--nomigrations` at least once in CI — SHOULD

To prove REQ-6 holds, the package's CI matrix should include a job that mimics the host's pytest invocation:

```bash
pytest --reuse-db --nomigrations
```

If this job fails, REQ-6 has regressed.

## Section 10: Packaging Requirements

### REQ-33: Distribution name and import name — MUST

- Distribution name (PyPI / pip install): `django-assets` (single distribution shipping `django_assets_core`, `django_assets_brokerage`, and `django_assets_trades` as separate Django apps; see ADR-0015)
- Import name: `django_assets_core`
- App label: `django_assets_core`

Matches README. Document the distinction prominently.

### REQ-34: Declare Django and Postgres ranges in `pyproject.toml` — MUST

```toml
[project]
requires-python = ">=3.11"
dependencies = [
    "Django>=4.2,<6.0",
    "djangorestframework>=3.14,<4.0",
    "psycopg[binary]>=3.1,<4.0",
]
```

PostgreSQL version is enforced at runtime (e.g., a startup check); pip cannot enforce it.

### REQ-35: Optional extras for connectors — SHOULD

Connector dependencies (e.g., `requests` for HTTP-based connectors, `ccxt` for crypto exchange) should be optional extras, not hard deps:

```toml
[project.optional-dependencies]
marketdata = ["requests>=2.31"]
crypto = ["ccxt>=4.0"]
```

The host will install only what it needs.

## Section 11: Verification Checklist (Pre-Release)

Before tagging `v0.1.0`, the package authors should verify the following against a Postgres 12 instance:

- [ ] Installs cleanly on Django 4.2 LTS and Python 3.12
- [ ] `pytest --reuse-db --nomigrations` passes with no extra host configuration (REQ-6 / REQ-32) — proves the `post_migrate` install path works
- [ ] `pytest` with full migrations also passes — proves the `migrations.RunSQL` install path works and matches the `post_migrate` SQL
- [ ] Both install paths read from the same canonical `.sql` file (REQ-6)
- [ ] `manage.py install_ledger_ddl` is idempotent and installs the same DDL as the migration and the signal (REQ-6a)
- [ ] DDL operations are idempotent under `--reuse-db` and on repeated `migrate` runs (REQ-7)
- [ ] No `ImportError` when `celery` is not installed (REQ-29)
- [ ] No `ImportError` when `requests` is not installed (REQ-35) — only when the HTTP connector is instantiated
- [ ] `manage.py spectacular --validate` produces zero errors in `dev_project/` (REQ-14)
- [ ] Standalone `dev_project/` runs `migrate`, `createsuperuser`, `runserver`, and a sample transaction round-trip
- [ ] `Instrument.resolve` and the default price connector are swappable via settings without code changes (REQ-24 / REQ-25)
- [ ] No `float` types appear in any serializer or model field (REQ-22)
- [ ] No naive datetimes accepted by any public API (REQ-21)
- [ ] No `LISTEN/NOTIFY`, no session-scoped advisory locks, no `SET` outside transactions (REQ-3)

## Out-of-Scope (Host-Side Work)

For completeness, the following are explicitly **not** package-side requirements — they belong to the future portfolio app inside the host:

- Mapping `auth.User` to one or more `Account` records
- Mapping the host's stock symbols and `options.models.OptionContract` records to `django_assets_core.Instrument` rows
- Building a custom `InstrumentResolver` that uses the host's symbol normalization
- Building an in-process `PriceConnector` that calls `options.services` and `stock` services directly
- Exposing user-facing DRF endpoints under `/v1/portfolio/...`
- Wiring DRF authentication and permissions for those endpoints

These are tracked separately and are the integration work that comes after `django-assets v0.1` ships.

## Related Documents

- `README.md` — package overview and design
- `django_assets_core_price_connectors_guide.md` — connector interface and examples
- `django_assets_core_extension_patterns_guide.md` — extension patterns for host applications
- `django_assets_brokerage_requirements.md` — high-level transaction template package
- `django_assets_trades_requirements.md` — trade grouping and tagging package
