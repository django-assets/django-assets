# ADR-0001: Use PostgreSQL as the only supported database

## Status

Accepted — 2026-06-02

## Context

`django-assets-core` relies on database-enforced integrity for its core correctness guarantees: balanced transaction legs, scale-checked decimal precision, and (in later versions) materialized holdings and partitioned ledger storage. These are implemented with PostgreSQL-specific features that have no portable equivalents:

- `NUMERIC` domains with `scale(VALUE) <= N` `CHECK` constraints
- Deferred constraint triggers (`DEFERRABLE INITIALLY DEFERRED`)
- Declarative range partitioning
- Generated columns
- Materialized views (planned for v0.4)

Supporting other backends (SQLite for tests, MySQL for a wider audience) would either silently drop these guarantees or require parallel implementations of every integrity check in application code — defeating the package's value proposition.

The original README already lists "cross-DB portability" as a non-goal. This ADR formalizes that scope across docs, tests, packaging, and the standalone `dev_project/`.

## Decision

PostgreSQL is the only supported database. The package:

- Targets PostgreSQL only in models, migrations, and integrity checks.
- Ships a standalone `dev_project/` that uses PostgreSQL — not SQLite, not any other backend.
- Documents PostgreSQL as a hard requirement in installation instructions.
- Does not include `db.sqlite3` or any other backend artifacts in `.gitignore` or templates.

## Consequences

**Easier:**

- All integrity guarantees can be expressed at the database layer without application-side duplication.
- No need to abstract over DB-specific features or write a query-builder dialect for ledger operations.
- Test environments match production semantics. Bugs caught in dev are bugs that would happen in prod.

**Harder:**

- Local development requires running PostgreSQL — no SQLite-in-a-file shortcut. Mitigated by documenting a one-line Docker invocation.
- Adopters wanting MySQL or SQL Server are blocked. This is intentional; the package targets correctness for financial data, which is incompatible with backends that lack the necessary integrity primitives.
- Test CI must provision PostgreSQL. Most CI providers handle this in one line.

## Related

- ADR-0002 sets the PostgreSQL minimum version.
