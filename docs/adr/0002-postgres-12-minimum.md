# ADR-0002: Target PostgreSQL 12 as the minimum version

## Status

Accepted — 2026-06-02

## Context

The primary target host's database version was verified to be **PostgreSQL 12.x** running on Linux, with TimescaleDB installed as an extension alongside `plpgsql`, `pg_stat_statements`, `pgstattuple`, and `pg_prewarm`.

Targeting a higher PostgreSQL version would block installation in the package's primary environment. The package authors have no leverage to push the host to upgrade Postgres for this dependency.

All features required by the current design are available in PostgreSQL 12:

- `NUMERIC` domains with `scale()` in `CHECK` constraints
- `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`
- Declarative range partitioning (PG 10+)
- Generated columns (PG 12+)
- `pg_advisory_xact_lock()` (transaction-scoped advisory locks; needed because the host uses pgBouncer transaction-pooling mode)

Features introduced in PostgreSQL 13+ that the package could otherwise have used are off-limits.

## Decision

PostgreSQL >= 12 is the supported version range.

Packaging:

- The package's documentation states PostgreSQL >= 12 as a hard requirement.
- A runtime startup check (or migration check) emits a clear error if a lower version is detected.
- CI runs against PostgreSQL 12 to prevent accidental adoption of newer features.

Features the package must not use:

- Multirange types (`int4multirange`, `tstzmultirange`) — PG 14+
- SQL/JSON `JSON_TABLE`, `JSON_QUERY` — PG 17+
- `pg_stat_io` — PG 16+
- `MERGE` statement — PG 15+
- `NULLS NOT DISTINCT` in unique indexes — PG 15+

The package must not require the TimescaleDB extension even though it is present in the host environment. Other adopters will not have it, and core correctness must not depend on it.

## Consequences

**Easier:**

- The package installs in the primary target environment.
- The supported PG range matches what most managed Postgres providers offer as a stable tier.

**Harder:**

- Some ergonomic newer features (`MERGE` for upserts in bulk-import paths, multirange types for time intervals) are unavailable. Workarounds exist but are wordier.
- PG 12 will leave LTS support in November 2024, after which adopters running unsupported PG will become a maintenance concern. The package authors should re-evaluate the floor when the target host upgrades.

**Optional future opt-in:**

- Because TimescaleDB is available on the primary host's database, a future optional add-on package could offer a TimescaleDB-hypertable variant of `transaction_legs` without infra changes for that host. The core's partitioning interface should leave room for an alternative implementation to be swapped in. This is not a v0.1 commitment.
