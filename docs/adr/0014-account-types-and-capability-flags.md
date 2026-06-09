# ADR-0014: Account capability flags and subtypes in brokerage

## Status

Accepted — 2026-06-03

Amended 2026-06-09 to add `allows_reconciliation` (see ADR-0024, ADR-0027, ADR-0028 for usage).

## Context

Real-world accounts carry functional and regulatory type information. A retail user might have a brokerage account, a cash account, a margin account, a Traditional IRA, a Roth IRA, a 401k, an HSA, a checking account, and a crypto wallet — all separate Accounts, all owned by the same User. These distinctions affect:

- Tax treatment of distributions and contributions (reporting concern).
- Whether short selling is allowed (host-level behavioral concern).
- Whether margin borrowing is allowed (host-level behavioral concern).
- Contribution limits and other regulatory caps (host-level concern).
- Which kinds of instruments can be held (broker policy concern).

None of these affect ledger integrity. The deferred balance trigger does not care whether an account is a brokerage or a checking account; it only validates per-instrument zero-sum across each Transaction. Per ADR-0020, core ships only numeric integrity, so all capability and subtype information lives in `django_assets.brokerage`, not core.

This ADR specifies the capability-flag and subtype fields and the forward-compatibility guarantees that govern how they evolve. The fields are added to a brokerage-side model that extends core's `Account`.

## Decision

### Schema location: `django_assets.brokerage.AccountProfile`

The `django_assets.brokerage` sub-package ships an `AccountProfile` model with a one-to-one relationship to `django_assets.Account` (per ADR-0015's single-app FK convention). Adopters that import from `django_assets.brokerage` get the capability fields available; adopters that only touch the `django_assets.core` sub-package work with plain Accounts.

```python
# django_assets/brokerage/models.py

class AccountProfile(models.Model):
    """Brokerage-specific extension of a core Account. Carries functional/regulatory
    type information and capability flags. Optional — installs only when the
    brokerage app is enabled."""
    account = models.OneToOneField(
        "django_assets.Account",
        related_name="brokerage_profile",
        on_delete=models.CASCADE,
    )

    subtype = models.CharField(max_length=40, blank=True, db_index=True)
    # Recommended values: "brokerage", "cash_account", "margin", "ira", "roth_ira",
    # "sep_ira", "simple_ira", "401k", "roth_401k", "403b", "hsa", "529",
    # "bank_checking", "bank_savings", "crypto_wallet", "crypto_exchange",
    # "custodial", "trust". Hosts may add their own.

    allows_short = models.BooleanField(default=False)
    # If True, non-currency holdings may go negative (short positions).

    allows_margin = models.BooleanField(default=False)
    # If True, currency holdings may go negative (margin borrowing).

    is_tax_advantaged = models.BooleanField(default=False, db_index=True)
    # Informational; affects reporting in trades/lots apps.

    allows_reconciliation = models.BooleanField(default=False, db_index=True)
    # If True, legs touching this account may be reconciled against broker
    # import lines (ADR-0024). Set during account setup for brokerage cash,
    # brokerage holdings, bank, and crypto-exchange accounts whose balances
    # are authoritatively reported by an external statement / feed / CSV.
    # Default False: a plain Account is just a bucket; reconciliation is opt-in.

    tax_treatment = models.CharField(max_length=40, blank=True)
    # Specific tax treatment when is_tax_advantaged=True. Recommended values:
    # "traditional_ira", "roth_ira", "sep_ira", "401k", "403b", "hsa", "529",
    # "uniform_transfer_to_minors", "trust".

    metadata = models.JSONField(default=dict, blank=True)
```

Core's `Account` itself stays narrow (per ADR-0020):

```python
# django_assets/core/models.py (unchanged shape per ADR-0020)
class Account(models.Model):
    id = models.BigAutoField(primary_key=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, ...)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
```

### `allows_reconciliation` gates the reconciliation linkage

This flag is the answer to "is this account broker-reported?" — the load-bearing question that ADR-0024 left open. It is set at account setup and determines whether legs touching the account can be added to `ImportLine.matched_legs` (per ADR-0026), and therefore whether they can become reconciled-and-locked under ADR-0024's signal handlers.

The flag is checked in three places:

- **The import orchestrator** (ADR-0027): when materializing an import line, only legs whose `account.brokerage_profile.allows_reconciliation` is True get added to `ImportLine.matched_legs`. Commission, fee, and counterparty legs in the same Transaction stay unreconciled.
- **Dedup match candidates** (ADR-0028): the search for a pre-existing manual Transaction to dedup against only considers legs on accounts with `allows_reconciliation=True`.
- **The end-of-batch invariant check** (ADR-0028): asserts that no import-origin Transaction has an `allows_reconciliation` leg outside `matched_legs`.

The flag may be toggled OFF as long as no `ImportLine.matched_legs` entry currently references a leg on this account. Once any reconciliation linkage exists, the host must first remove those linkages (the normal unflip workflow per ADR-0024) before the flag can be cleared. Brokerage's `AppConfig.ready()` wires a `pre_save` handler on `AccountProfile` that enforces this — unsetting `allows_reconciliation` while reconciled legs exist raises rather than silently un-locking those legs.

Unlike the `allows_short` / `allows_margin` capability flags (whose enforcement is the host's job, see below), `allows_reconciliation` is enforced by brokerage itself because it is wired into the reconciliation invariants. Hosts that install only core get a field with no enforcement and no consumers — harmless, but meaningless.

### Enforcement is the host's job

Brokerage templates MAY read capability flags before generating transactions (e.g., `short_shares` may refuse to operate on an account where `account.brokerage_profile.allows_short` is False). The ledger does NOT enforce:

- Short-sale refusal in cash-only accounts.
- Margin refusal in non-margin accounts.
- Contribution limits against tax-advantaged accounts.
- Instrument-kind restrictions per account subtype.

These are policy decisions made in templates or host code. The deferred balance trigger is the only universal integrity rule.

### Forward-compatibility guarantees

The schema is designed to be extensible without breaking changes within a major version:

1. **No PostgreSQL `ENUM` types.** All discriminators (`subtype`, `tax_treatment`) are `CharField` with Python-layer `choices=` validation. PG `ENUM` types are brittle to modify (`ALTER TYPE ADD VALUE` has restrictions; values cannot be removed; the value list lives in DDL). CharField accepts any string at the DB layer; validation is application-side and easy to update.

2. **No DB-level `CHECK` constraints restricting enum value sets.** The DB column accepts any string; Django's `choices=` provides validation that can be updated without DDL changes.

3. **New boolean flag columns can be added freely.** PostgreSQL 12+ supports `ALTER TABLE ADD COLUMN ... DEFAULT False` as a non-locking metadata-only operation when the default is a constant. Adding new flags like `allows_options` or `allows_fractional_shares` is fast even on large tables.

4. **New `subtype` and `tax_treatment` values are documentation-only.** No schema change required to start using `"hsa_family"`, `"529_custodial"`, `"crypto_cold_storage"`, etc.

5. **`AccountProfile.metadata` (and `Account.metadata` in core) is the experimentation surface.** Any field not yet stable enough to deserve a column lives in `metadata`. When it stabilizes and warrants indexing, it gets promoted to a column.

6. **No removal of existing flags within a major version.** Deprecated fields stay in the schema until a major version boundary.

7. **No default-value changes for existing flags within a major version.** A flag's default is its semantic contract; changing it would silently change behavior. New behavior is introduced as a new flag.

8. **Default values represent "not set" semantics.** Booleans default to `False` (= "not enabled"). CharFields default to `""` (= "not specified"). New flags don't accidentally enable behavior for existing rows.

### Subtype suggested values

`django_assets.brokerage` ships a documented list of recommended `subtype` values for retail US contexts. Hosts targeting other jurisdictions or institutional contexts extend as needed.

| `subtype` | Description |
| --- | --- |
| `brokerage` | General taxable brokerage account |
| `cash_account` | Brokerage with cash settlement only (no margin) |
| `margin` | Brokerage with margin enabled |
| `ira` | Traditional IRA |
| `roth_ira` | Roth IRA |
| `sep_ira` | SEP IRA |
| `simple_ira` | SIMPLE IRA |
| `401k` | 401(k) |
| `roth_401k` | Roth 401(k) |
| `403b` | 403(b) |
| `hsa` | Health Savings Account |
| `529` | 529 college savings |
| `bank_checking` | Bank checking |
| `bank_savings` | Bank savings |
| `crypto_wallet` | Self-custody crypto wallet |
| `crypto_exchange` | Crypto exchange account |
| `custodial` | Custodial account (UTMA/UGMA) |
| `trust` | Trust account |

`tax_treatment` follows similar conventions when `is_tax_advantaged=True`.

## Consequences

**Easier:**

- Core stays unopinionated (per ADR-0020). Account is just (owner, name, metadata).
- Hosts that install only core get a minimal Account model with no policy fields.
- Brokerage owns the policy surface for accounts. Capability flags evolve in brokerage without touching core.
- The schema generalizes to account types not yet anticipated. New retail products (crypto staking accounts, fractional-share platforms, prediction-market accounts) need no schema changes.
- Queryable behavior: `AccountProfile.objects.filter(allows_short=True).select_related("account")` is fast.
- Indexed `is_tax_advantaged` filter supports fast tax reporting.

**Harder:**

- Brokerage installation becomes more meaningful: without it, capability flags don't exist. Most adopters who care about capability flags will install brokerage anyway, but documentation must be clear.
- The OneToOne relationship adds one query for capability reads. `select_related` mitigates; indexed FKs make the join trivial.
- The `subtype` and `tax_treatment` fields are not strictly typed at the DB layer. Hosts that want strict validation add CHECK constraints or model-layer validation themselves.

**Deferred:**

- Optional enforcement mode (`DJANGO_ASSETS_ENFORCE_ACCOUNT_FLAGS`) if demand emerges.
- Additional first-class flag columns (`allows_options`, `allows_fractional_shares`, etc.) as needed. The migration pattern is non-locking; can be added in any minor release.
- Per-jurisdiction tax-treatment vocabulary (UK ISAs, Canadian TFSAs/RRSPs, etc.). Hosts targeting other markets use their own values in `tax_treatment` without package changes.

## Related

- ADR-0020 (Core ships only numeric integrity) — the principle that moved capability flags from core to brokerage.
- ADR-0005 establishes single-owner accounts.
- ADR-0006 establishes CASCADE on user delete.
- ADR-0015 (single PyPI distribution) — `AccountProfile` ships in brokerage, alongside other opinionated extensions.
- ADR-0024 (Reconciliation scope) — consumes `allows_reconciliation` to determine which legs are eligible for the reconciliation lock.
- ADR-0027 (Import schema registration) — the orchestrator filters `matched_legs` candidates by `allows_reconciliation`.
- ADR-0028 (Transaction provenance) — the dedup query filters candidates by `allows_reconciliation` on the matched-leg's account.
- OQ-6 in `open-questions.md` is resolved by this ADR.
