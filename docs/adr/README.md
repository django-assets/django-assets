# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs) for `django-assets-core` and the sibling packages `django-assets-brokerage` and `django-assets-trades`.

## Format

Each ADR is a short markdown file capturing one decision in Michael Nygard's format:

- **Status** — Proposed / Accepted / Deprecated / Superseded
- **Context** — the forces at play that motivated the decision
- **Decision** — the choice made
- **Consequences** — what becomes easier and what becomes harder

ADRs are **living documents**. If a decision changes, edit the ADR in place to reflect the new decision. Update the `Date` line. Status values: `Proposed`, `Accepted`, `Deprecated` (the decision no longer applies and is not replaced).

ADRs are numbered sequentially in zero-padded four-digit form (`0001`, `0002`, …) and named in kebab-case. Numbers are not reused; if a decision becomes obsolete, mark its ADR `Deprecated` rather than renumbering.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-postgres-only.md) | Use PostgreSQL as the only supported database | Accepted |
| [0002](0002-postgres-12-minimum.md) | Target PostgreSQL 12 as the minimum version | Accepted |
| [0003](0003-django-42-minimum.md) | Target Django 4.2 LTS as the minimum version | Accepted |
| [0004](0004-ddl-install-hybrid.md) | Install non-table DDL via hybrid migration + post_migrate, with external override | Accepted |
| [0005](0005-account-single-owner.md) | Account has a single owner | Accepted |
| [0006](0006-account-cascade-on-user-delete.md) | Cascade Account deletion when User is hard-deleted | Accepted |
| [0007](0007-portfolio-as-query.md) | Portfolio is a query class, not a stored entity | Accepted |
| [0008](0008-auth-user-model-reference.md) | Reference the user model via settings.AUTH_USER_MODEL only | Accepted |
| [0009](0009-instrument-identity-model.md) | Instrument identity tracks legal security, not venue | Accepted |
| [0010](0010-option-contract-model.md) | Option contract model in the brokerage sub-package — OptionMeta, Deliverable, CorporateAction | Accepted |
| [0011](0011-core-is-the-ledger.md) | Core does not track corporate actions, ingest broker feeds, or own external reality | Accepted |
| [0012](0012-transactions-are-atomic-events.md) | Transaction settlement and trade timestamps | Accepted |
| [0013](0013-units-of-value-are-instruments.md) | All units of value are first-class Instruments | Accepted |
| [0014](0014-account-types-and-capability-flags.md) | Account capability flags and subtypes in the brokerage sub-package | Accepted |
| [0015](0015-single-pypi-distribution.md) | Single PyPI distribution, single Django app, organized into sub-packages | Accepted |
| [0016](0016-holdings-via-live-aggregation.md) | Holdings via live aggregation; no Holding table; minimal indexes | Accepted |
| [0017](0017-admin-and-drf-surfaces.md) | Admin and DRF surfaces in core | Accepted |
| [0018](0018-instrument-resolver-default.md) | Instrument resolver — default normalization and API shape | Accepted |
| [0019](0019-bulk-import-and-management.md) | Bulk import primitives in the core sub-package; import management in the brokerage sub-package | Accepted |
| [0020](0020-core-ships-only-numeric-integrity.md) | Core ships only numeric integrity | Accepted |
| [0021](0021-brokerage-template-fee-handling.md) | Brokerage sub-package templates follow the source's transaction shape | Accepted |
| [0022](0022-no-append-only-enforcement.md) | Append-only enforcement is not shipped | Accepted |
| [0023](0023-disclosure-transactions.md) | Recording disclosures of previously-hidden transaction details | Proposed |
| [0024](0024-reconciliation-scope.md) | Reconciliation scope — asset-account legs only, reconciliation system in brokerage | Accepted |
| [0025](0025-broker-download-lines.md) | Broker download lines — storage and matching workflow | Accepted |
| [0026](0026-importline-to-leg-relationship.md) | ImportLine → TransactionLeg relationship (auto-generated M2M, no through model) | Accepted |
| [0027](0027-import-schema-registration.md) | Broker import schemas — code-only registry, four-part natural key | Proposed |
| [0028](0028-transaction-provenance.md) | Transaction provenance — origin marker and import dedup matching | Proposed |

## Open questions

Questions that have been surfaced but not yet decided. Each will become an ADR once resolved. See [open-questions.md](open-questions.md).

## Pending host confirmations

Decisions that hinge on input from the primary target host developer rather than the package authors are tracked in [pending-host-confirmations.md](pending-host-confirmations.md).

## Writing a new ADR

1. Pick the next number (look at the index above; reserve a slot by adding an entry).
2. Copy an existing ADR as a template, or use the structure: `# ADR-NNNN: Title` followed by `## Status`, `## Context`, `## Decision`, `## Consequences`.
3. Status starts at `Proposed`. Move to `Accepted` when the decision is committed.
4. Keep it short. ADRs are not design documents — they capture one decision and the rationale. If the explanation runs long, link out to a fuller doc.
5. Update the index in this README.

## Editing an existing ADR

1. Edit the file directly to reflect the new state of the decision.
2. Update the `Status` date line.
3. Update the index in this README if the title or status changed.
4. If a decision is being abandoned entirely without replacement, mark it `Deprecated` rather than deleting the file.
