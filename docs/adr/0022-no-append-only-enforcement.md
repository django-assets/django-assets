# ADR-0022: Append-only enforcement is not shipped

## Status

Accepted — 2026-06-03

## Context

Proper double-entry ledgers in regulated contexts typically enforce **append-only** semantics: once a Transaction is posted, it cannot be edited or deleted. Corrections happen by posting offsetting Transactions (the reversal pattern). This preserves audit trail and matches the practice required by broker-dealer regulations, institutional custody systems, and other audited financial environments.

The package could ship an opt-in mode for this:

```python
# settings.py (hypothetical)
DJANGO_ASSETS_ENFORCE_APPEND_ONLY = True
```

When enabled, this would wire signal handlers that reject mutation of persisted Transaction or TransactionLeg rows and expose helpers like `Transaction.void(reason)` that post offsetting legs.

The question is whether to include such a mode in the `django-assets` distribution.

Two considerations argue against:

1. **The deferred balance trigger from ADR-0004 already enforces the only thing that matters for ledger correctness** — per-instrument zero-sum per Transaction. Any mutation that would violate that constraint is rejected at COMMIT. Mutation that preserves balance (editing timestamps, descriptions, metadata, or making offsetting amount adjustments that still net to zero) is mathematically harmless to the ledger's integrity.

2. **Append-only is a workflow/policy concern, not a numeric-integrity concern.** Per ADR-0020, core ships only numeric integrity. Workflow policies (mutation discipline, audit trails, regulatory compliance) belong above core. Different regulated contexts have different specific requirements (some require pre-mutation review; some require dual-control approval; some require immutable archival to write-once storage); building a generic enforcement mode that satisfies all of them is out of scope.

A third consideration: hosts that genuinely need append-only enforcement have ecosystem-standard tools available. `django-simple-history`, `django-auditlog`, custom pre-save signal handlers, write-protected database roles, and OS-level immutability flags are all options. None require package-side support.

## Decision

`django-assets` does not ship append-only enforcement. The package:

- Allows `Transaction` and `TransactionLeg` rows to be edited and deleted via the ORM, the admin (per ADR-0017), or any other standard Django mechanism.
- Relies on the deferred balance trigger from ADR-0004 to catch any mutation that would break per-instrument zero-sum. The trigger fires on INSERT, UPDATE, and DELETE.
- Documents the **reversal pattern** as the recommended approach for production data corrections in regulated contexts (a brief example was included in ADR-0017). The pattern is best practice but not enforced.
- Does NOT ship `DJANGO_ASSETS_ENFORCE_APPEND_ONLY` or any similar setting.
- Does NOT ship a `Transaction.void(reason)` helper.
- Does NOT ship pre-save/pre-delete signal handlers that reject mutation.

Hosts that need append-only enforcement build it themselves. Common patterns:

- Override `Transaction.save()` and `Transaction.delete()` in a subclass.
- Wire `pre_save` and `pre_delete` signal handlers in the host's app.
- Use `django-simple-history` or `django-auditlog` for immutable audit trails alongside live editing.
- Use database role permissions to restrict who can mutate ledger tables.
- Use write-once archival storage for compliance-period preservation.

The package does not endorse one approach over another. Hosts choose based on their regulatory and operational requirements.

## Consequences

**Easier:**

- The package stays narrowly scoped to numeric integrity (per ADR-0020). No opt-in policy machinery to design, document, test, or maintain.
- Adopters who don't need append-only get a simpler model and admin experience. Most adopters fall in this category.
- Regulated adopters can wire whatever specific append-only mode their regulator requires, without fighting a generic implementation that doesn't quite fit.

**Harder:**

- Regulated adopters get less out of the box. They have to build the enforcement layer themselves.
- The "fully editable admin" default (per ADR-0017) is documented as the right choice in most contexts but may surprise adopters coming from systems where append-only is assumed.
- Documentation must clearly explain that ledger integrity is enforced (via the balance trigger) but mutation discipline is not.

**Deferred:**

- A sibling package (e.g., `django-assets-audit` or `django-assets-append-only`) that bundles common enforcement patterns. Could be built by a contributor if demand emerges. Not part of the core distribution.

## Related

- ADR-0004 establishes the deferred balance trigger that enforces the only mutation-relevant integrity constraint.
- ADR-0017 establishes the fully-editable admin default and documents the reversal pattern as best practice.
- ADR-0020 establishes the principle that core ships only numeric integrity; workflow policies belong above core.
- OQ-15 in `open-questions.md` is resolved by this ADR.
