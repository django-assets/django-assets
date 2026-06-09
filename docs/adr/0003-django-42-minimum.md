# ADR-0003: Target Django 4.2 LTS as the minimum version

## Status

Accepted — 2026-06-02

## Context

The primary target host pins Django 4.2 LTS in its `requirements.txt`. The host's release schedule does not include a near-term Django 5.x upgrade.

The package's original README stated Django >= 5.0 as a minimum. Holding that line would prevent installation in the primary target environment. The package authors have no leverage to push the host's Django upgrade.

Django 4.2 is an LTS release with extended security support through April 2026, after which the host will need to upgrade or remain on an unsupported version. That is a host concern, not a package concern.

The package must avoid features added in Django 5.x+ that are unavailable on 4.2:

- `models.GeneratedField` (Django 5.0+) — for computed columns, use raw SQL migrations or PostgreSQL `GENERATED ALWAYS AS` instead.
- `db_default=` field argument (Django 5.0+) — use `default=` only.
- Composite primary keys (Django 5.2+) — use single-column `BigAutoField` PKs.
- ORM features added in 5.0+ for `Q` reference shortcuts and async ORM improvements.

DRF on the host is pinned to 3.14.0. Avoid features added in DRF 3.15+ (some serializer field arguments and schema helpers).

## Decision

The supported Django range is `Django>=4.2,<6.0`. Concretely:

- `pyproject.toml` declares `"Django>=4.2,<6.0"` as a dependency.
- CI matrix tests against Django 4.2 LTS as the floor and at least one current 5.x release as the ceiling.
- The package documents 4.2 LTS as the minimum and notes that 4.2 reaches end-of-life in April 2026.
- The package avoids the 5.x-only features listed above.

DRF dependency: `"djangorestframework>=3.14,<4.0"`. Avoid DRF 3.15+ features.

## Consequences

**Easier:**

- The package installs in the primary target environment without forcing the host to upgrade Django.
- 4.2 LTS is widely deployed; supporting it broadens the package's potential adopter base.

**Harder:**

- The package cannot use `GeneratedField` for scaled-integer generated columns; raw SQL `GENERATED ALWAYS AS ... STORED` is required instead, paired with the hybrid DDL install path (see ADR-0004).
- The 4.2 LTS support window closes in April 2026. Once the host upgrades past 4.2 (or once 4.2 reaches EOL), this ADR should be revisited and likely superseded with a higher floor.
- Avoiding 5.x-specific ORM ergonomics is a small ongoing tax during development; mitigated by linter rules or a periodic audit.
