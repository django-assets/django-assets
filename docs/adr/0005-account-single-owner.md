# ADR-0005: Account has a single owner

## Status

Accepted — 2026-06-02

## Context

The package needs to model who owns a brokerage / wallet / bank account. Several patterns are common in financial software:

1. **Single owner.** `Account.owner = ForeignKey(User)`. One account, one owner.
2. **Membership table.** `AccountMembership` rows associate users with accounts in a many-to-many relation. Models joint accounts, advisor access, family sharing.
3. **Organization-owned accounts.** Account belongs to an `Organization`, users belong to organizations via roles. Models institutional setups.

The package's primary target is a retail-focused platform where one user = one customer. The host has no concept of shared accounts. Cross-customer data isolation is enforced at the platform layer.

Adding a `Membership` table later is a non-breaking change (new optional table, no schema migration on existing records). Removing one is a breaking schema change. The conservative path is to start with the simpler model.

## Decision

`Account` has exactly one owner, modeled as a `ForeignKey` to the user model:

```python
class Account(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="accounts",
        db_index=True,
    )
```

(`on_delete=CASCADE` is established in ADR-0006. The user model is referenced as a string per ADR-0008.)

The package does not ship an `AccountMembership` model, account-sharing logic, or organization-scoped account ownership in v0.x.

Hosts that need shared accounts must build it themselves at a layer outside the package. The package will not absorb multi-tenant or shared-ownership logic into core — those concerns belong to the host application.

## Consequences

**Easier:**

- The schema is simple. One FK chain: `User -> Account -> Transaction -> TransactionLeg`.
- All Transactions in an Account are unambiguously owned by exactly one User. Authorization checks at the host level reduce to "does this User own this Account?"
- The single-owner constraint combines with CASCADE deletion to make the deferred balance trigger trivially correct (see ADR-0006).
- No migration pain for v0.1.

**Harder:**

- Joint accounts, advisor view, family-account-tree, and institutional patterns are not supported by core. Adopters needing them must build at a layer outside the package, accept fork-and-patch, or wait for a future major version to introduce a swappable `AbstractAccount`.
- The "user has many accounts at a single brokerage" pattern (cash + margin + IRA) still works — those are separate `Account` rows owned by the same user. No change needed.
- Multi-tenancy (one DB serving multiple isolated customers) is not addressed by this decision and is also out of scope for v0.x.

## Related

- ADR-0006 covers the `on_delete` behavior when a User is hard-deleted.
- ADR-0008 covers how the user model is referenced.
