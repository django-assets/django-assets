# ADR-0006: Cascade Account deletion when User is hard-deleted

## Status

Accepted — 2026-06-02

## Context

When a `User` row is deleted, what should happen to that user's `Account` records and the financial history below them?

Two common defaults:

- **`PROTECT`** — refuse the delete if any Account exists. Forces host code to explicitly close out, transfer, or archive accounts before allowing the user to be deleted. Safer for ledgers because financial history can't be lost by accident.
- **`CASCADE`** — delete the user, delete all their Accounts, delete all Transactions on those Accounts, delete all TransactionLegs. Irreversible, but matches an explicit "remove everything" intent.

The target host's user-deletion semantics inform the right default. The host's user-delete webhook does not actually delete the user row — it just sets `is_active = False`. The `auth.User` row stays in the database indefinitely. This is a common pattern in Django applications where account deactivation is preferred over hard deletion.

No other code path in the host performs a hard `User.delete()` during normal operation. Other host-side FKs to `auth.User` use `CASCADE` already, but the cascade never fires because the User row is never actually deleted in normal use.

The only realistic scenario where a User row is genuinely deleted is a GDPR Article 17 (right to erasure) request. In that case, the data MUST be gone — not flagged inactive, not preserved for accounting, not archived. Article 17 requires actual erasure.

With `PROTECT`, every GDPR request requires the host to write a multi-step "find all Accounts, void all Transactions, archive somewhere, delete Accounts, delete User" dance. With `CASCADE`, it's one `User.delete()` call.

There is one further subtlety: the deferred balance trigger fires on DELETE as well as INSERT and UPDATE. When CASCADE removes *all* legs of a Transaction together, the per-instrument sums stay at `0 = 0` and the trigger passes. Because Account has a single owner (ADR-0005) and Transactions hang off Account, partial deletion that would orphan legs and break balance cannot occur. The single-owner design plus CASCADE is internally watertight.

## Decision

`Account.owner` uses `on_delete=models.CASCADE`. Hard deletion of a User cascades:

```
User.delete()
  -> Account.delete()       (cascaded)
    -> Transaction.delete() (cascaded)
      -> TransactionLeg.delete() (cascaded)
```

Reference data (`Instrument`, `Exchange`, `Identifier`) is unaffected — those are shared catalog rows, not user-owned.

The package documents this behavior prominently in `Account.owner`'s docstring and in the package README's GDPR / data deletion section.

## Consequences

**Easier:**

- GDPR Article 17 compliance is a one-call operation in the host.
- The semantic is internally consistent with the single-owner design (ADR-0005). Partial cascades that would break ledger balance are structurally impossible.
- The package matches the host's existing convention (other FKs to `auth.User` already use CASCADE).

**Harder:**

- Adopters with stricter ledger-preservation requirements (institutional custody, regulated brokerage) cannot use the shipped `Account` model as-is. They must subclass and override the FK, or wait for a future swappable `AbstractAccount` (see ADR-0005's "Harder" section).
- A bug or operator error that calls `User.delete()` accidentally wipes financial history with no recovery. This risk is intentional: the host's normal flow is soft delete via `is_active = False`; calling `.delete()` is always an explicit, deliberate act in this target environment.
- The default is opinionated. Future adopters surprised by CASCADE on financial data must be redirected to the documented rationale.

## Future override

If demand from other adopters for `PROTECT` semantics becomes broad, a swappable `AbstractAccount` may be introduced in a future minor version, letting hosts replace the concrete model. Not required for v0.1.

## Related

- ADR-0005 establishes single-owner accounts.
- ADR-0008 covers the user model reference.
